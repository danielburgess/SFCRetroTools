"""Build pipeline: BuildSpec → output ROM file.

Steps:
  1. Copy original ROM into a working bytearray (SMC header stripped, kept aside).
  2. Iterate sections in declaration order; dispatch each to its handler.
  3. Post-process: revbyteloc patch, pad to next SNES size, fix checksum.
  4. Reattach SMC header (if original had one), write to `out_path`.

Diff output (xdelta/IPS) lands in Phase 4.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Callable, Optional

from retrotool.core.cache import BuildCache, sha256_file, sha256_many
from retrotool.core.rom import SMC_HEADER_SIZE, _strip_smc_header, detect_header
from retrotool.build.diff import DiffResult, write_diff
from retrotool.build.handlers import BuildContext, HandlerError, WriteRange, get_handler
from retrotool.build.overflow import FreespaceAllocator
from retrotool.build.interpolate import evaluate_condition
from retrotool.build.spec import BuildSpec, Section, SectionKind


# Section kinds with writes deterministic from attrs+inputs. <asar> and
# <project> are NOT in this set by default: their output can in principle
# depend on assembler state we don't track (reads of prior ROM bytes,
# macro-computed freespace, etc.). A user who has verified their patch is
# a pure write-set can opt one in per-section with `cache="1"`, which
# also activates diff-mode writes in handle_asar + transitive incsrc /
# incbin hashing in _section_cache_key. Conversely, `cache="0"` forces
# caching off for an otherwise cacheable kind.
_CACHEABLE_KINDS = frozenset({
    SectionKind.REP, SectionKind.INS, SectionKind.BIN,
    SectionKind.GRAPHICS, SectionKind.SCRIPT,
    SectionKind.FIXED_RECORDS, SectionKind.WINDOWED_SCRIPT,
})

# Kinds eligible for explicit opt-in (cache="1"). These need extra work to
# cache safely — see handle_asar's diff-mode path for the asar story.
_OPT_IN_CACHEABLE_KINDS = frozenset({
    SectionKind.ASAR,
})

# Bumped when the cache-key schema changes; old entries are ignored on read.
# v3: cache artifact reframed to support multi-range writes per section.
# v4: key now hashes all typed Section fields that affect handler output
#     (offset, stride, count, fields, fallback_table, pointer_table,
#     pointer_size, terminator, word_wrap, overflow, placement, textbuf_limit,
#     codec). Pre-v4 keys are silently invalidated.
# v5: ASAR sections opted in via cache="1" hash the transitive incsrc /
#     incbin dependency tree, plus `defines` / `includes` / `allow-shrink`
#     attrs. cache override flag itself is part of the key so flipping it
#     invalidates.
_CACHE_VERSION = "5"

# Multi-write cache artifact framing:
#   magic  = b"RTMW"        (4 bytes)
#   version= u8 = 1
#   count  = u32 little-endian
#   then `count` records of:  offset u64le, length u32le, data[length]
_CACHE_MAGIC = b"RTMW"
_CACHE_FRAME_VERSION = 1


def _pack_writes(rom: bytes, writes: list[WriteRange]) -> bytes:
    parts: list[bytes] = [_CACHE_MAGIC, bytes([_CACHE_FRAME_VERSION])]
    parts.append(len(writes).to_bytes(4, "little"))
    for wr in writes:
        data = bytes(rom[wr.offset:wr.end])
        parts.append(wr.offset.to_bytes(8, "little"))
        parts.append(len(data).to_bytes(4, "little"))
        parts.append(data)
    return b"".join(parts)


def _unpack_writes(blob: bytes) -> list[tuple[int, bytes]]:
    if not blob.startswith(_CACHE_MAGIC):
        raise ValueError("cache artifact missing RTMW magic")
    pos = len(_CACHE_MAGIC)
    ver = blob[pos]; pos += 1
    if ver != _CACHE_FRAME_VERSION:
        raise ValueError(f"unknown cache frame version {ver}")
    count = int.from_bytes(blob[pos:pos + 4], "little"); pos += 4
    out: list[tuple[int, bytes]] = []
    for _ in range(count):
        off = int.from_bytes(blob[pos:pos + 8], "little"); pos += 8
        ln = int.from_bytes(blob[pos:pos + 4], "little"); pos += 4
        out.append((off, blob[pos:pos + ln]))
        pos += ln
    return out


def _is_cacheable(section: Section) -> bool:
    """Resolve per-section cache override against the default kind set.

    - `cache=True`  always caches (must be in _CACHEABLE_KINDS OR
      _OPT_IN_CACHEABLE_KINDS; other kinds aren't wired yet).
    - `cache=False` always skips cache.
    - `cache=None`  follows _CACHEABLE_KINDS.
    """
    if section.cache is False:
        return False
    if section.cache is True:
        return section.kind in _CACHEABLE_KINDS or section.kind in _OPT_IN_CACHEABLE_KINDS
    return section.kind in _CACHEABLE_KINDS


def _section_cache_key(section: Section, files_root: Path) -> Optional[str]:
    """SHA-256 over section kind + typed fields + attrs + input-file content hashes.

    All typed Section fields that influence handler output must be hashed
    here. DataDef-derived sections have an empty `attrs` dict — their
    configuration lives entirely in typed fields (stride, count, fields,
    pointer_table, word_wrap, overflow, ...), so hashing `attrs` alone
    would collide whenever two datadefs point at the same input file.

    For ASAR sections opted in via `cache="1"`, the transitive incsrc /
    incbin dependency tree is walked and hashed. See
    `retrotool.build.asar_deps.scan_deps` for limits.

    Returns None if the section is not cacheable (kind default + override)
    or any input file is missing (handler will raise its own error)."""
    if not _is_cacheable(section):
        return None
    parts: list[bytes] = [f"v{_CACHE_VERSION}".encode(), section.kind.value.encode()]
    # Hash the override flag so toggling cache on/off produces distinct keys.
    parts.append(f"__cache_override__={section.cache!r}".encode())
    # Stable attr ordering — we hash dict items, not the dict.
    for k in sorted(section.attrs):
        parts.append(f"{k}={section.attrs[k]}".encode())
    # Typed Section fields. Stored under `__name__` to avoid colliding with
    # any same-named attr from the front-end. `repr()` gives a stable form
    # for primitives, dicts (py≥3.7 insertion-ordered), and lists.
    typed_fields = (
        "offset", "size", "grow", "codec",
        "count", "stride", "fields",
        "pointer_table", "pointer_size", "terminator",
        "word_wrap", "overflow", "placement",
        "textbuf_limit", "pad_to", "bpp", "dedupe",
        "condition", "from_datadef",
    )
    for name in typed_fields:
        parts.append(f"__{name}__={repr(getattr(section, name, None))}".encode())
    for f in section.files:
        path = (files_root / Path(str(f))).resolve()
        if not path.exists():
            return None
        parts.append(sha256_file(path).encode())
    if section.table is not None:
        tpath = (files_root / Path(str(section.table))).resolve()
        if tpath.exists():
            parts.append(sha256_file(tpath).encode())
    if section.fallback_table is not None:
        fpath = (files_root / Path(str(section.fallback_table))).resolve()
        if fpath.exists():
            parts.append(sha256_file(fpath).encode())
    # ASAR opt-in: walk incsrc/incbin transitive deps and hash every file.
    # The entry file is already hashed via `section.files` above, but deps
    # aren't — this adds them. Also includes/defines are part of the key.
    if section.kind == SectionKind.ASAR and section.cache:
        from retrotool.build.asar_deps import scan_deps
        raw = section.attrs
        include_dirs: list[Path] = []
        for p in (raw.get("includes") or "").split("|"):
            if p:
                include_dirs.append((files_root / Path(p)).resolve())
        for fspec in section.files:
            entry = (files_root / Path(str(fspec))).resolve()
            if not entry.exists():
                continue
            for dep in scan_deps(entry, include_dirs=include_dirs):
                # `entry` itself is re-hashed here — cheap and keeps scan_deps
                # self-contained (the caller doesn't need to skip deps[0]).
                parts.append(f"asar_dep={dep}".encode())
                parts.append(sha256_file(dep).encode())
        # Pin the other asar-handler-visible attrs explicitly so trailing
        # attr additions don't accidentally collide or invalidate.
        for k in ("includes", "defines", "allow-shrink"):
            parts.append(f"asar_attr_{k}={raw.get(k, '')}".encode())
    return sha256_many(parts)


@dataclass
class SectionResult:
    section: Section
    # All byte ranges this section wrote (in emission order). A handler that
    # emits one contiguous span yields a single-element list; multi-write
    # handlers (e.g. the script pointer-table + inline + tails) yield many.
    write: list[WriteRange]
    cache_hit: bool = False


@dataclass
class BuildResult:
    rom_path: Path
    rom_size: int
    sections: list[SectionResult] = field(default_factory=list)
    skipped: list[Section] = field(default_factory=list)
    checksum: Optional[int] = None
    duration_ms: int = 0
    diffs: list["DiffResult"] = field(default_factory=list)
    cache_hits: int = 0


# Valid SNES ROM sizes (post-header-strip). Pad rounds up to the next entry.
_SNES_PAD_SIZES = [
    0x80_000, 0x100_000, 0x200_000, 0x400_000,
    0x600_000, 0x800_000, 0xC00_000, 0x1000_000,
]


def _pad_to_next_size(rom: bytearray, pad_byte: int = 0x00) -> int:
    """Pad with `pad_byte` to the next valid SNES ROM size. Returns final size."""
    cur = len(rom)
    fill = bytes([pad_byte & 0xFF])
    for size in _SNES_PAD_SIZES:
        if cur <= size:
            if size != cur:
                rom.extend(fill * (size - cur))
            return size
    raise HandlerError(f"ROM size {cur:#x} exceeds max supported ({_SNES_PAD_SIZES[-1]:#x})")


def _compute_checksum(rom: bytes) -> int:
    """Sum of all bytes mod 0x10000. Standard SNES checksum."""
    return sum(rom) & 0xFFFF


def _patch_checksum(rom: bytearray) -> Optional[int]:
    """Detect header, recompute checksum + complement, write back. Returns new checksum."""
    h = detect_header(bytes(rom))
    if h is None:
        return None
    # Zero the existing checksum bytes before summing so the result is stable.
    off = h.header_offset
    rom[off + 0x1C:off + 0x20] = b"\xFF\xFF\x00\x00"
    csum = _compute_checksum(bytes(rom))
    comp = csum ^ 0xFFFF
    rom[off + 0x1C] = comp & 0xFF
    rom[off + 0x1D] = (comp >> 8) & 0xFF
    rom[off + 0x1E] = csum & 0xFF
    rom[off + 0x1F] = (csum >> 8) & 0xFF
    return csum


def _section_kinds_filter(
    only: Optional[set[str]], skip: Optional[set[str]]
) -> Optional[Callable[[Section], bool]]:
    """Return a predicate(section) → True if the section should run.

    Matchable identifiers per section:
      * `section.kind.value` — e.g. "asar", "script"
      * `section.from_datadef` — DataDef name (for DataDef-derived sections)
      * `section.attrs["name"]` / `section.attrs["alias"]` — user-set on
        inline `[[rom.build.sections]]` entries that lack a DataDef
      * `section.source` and its ":"-suffix — e.g. "project.toml:sections[5]"
        and "sections[5]"
      * Positional aliases for inline sections: `sections[N]` also matches
        the singular `section[N]`, so either spelling works on the CLI.
    """
    if not only and not skip:
        return None
    only_l = {s.lower() for s in (only or set())}
    skip_l = {s.lower() for s in (skip or set())}

    def _ids(section: Section) -> set[str]:
        ids: set[str] = {section.kind.value.lower()}
        if section.from_datadef:
            ids.add(section.from_datadef.lower())
        # User-set alias / name on inline sections.
        for key in ("name", "alias"):
            v = section.attrs.get(key)
            if isinstance(v, str) and v:
                ids.add(v.lower())
        if section.source:
            # section.source is typically "datadef:<name>" or
            # "<path>:sections[N]". Match the raw string, the ":"-suffix,
            # and — for positional inline refs — the singular form.
            src = section.source.lower()
            ids.add(src)
            if ":" in src:
                suffix = src.split(":", 1)[1]
                ids.add(suffix)
                if suffix.startswith("sections[") and suffix.endswith("]"):
                    ids.add("section[" + suffix[len("sections["):])
        return ids

    def keep(section: Section) -> bool:
        ids = _ids(section)
        if only_l and ids.isdisjoint(only_l):
            return False
        if skip_l and not ids.isdisjoint(skip_l):
            return False
        return True

    return keep


def build(
    spec: BuildSpec,
    *,
    source_root: Path,
    out_path: Path,
    original_rom: Optional[Path] = None,
    cache: Optional[BuildCache] = None,
    only: Optional[set[str]] = None,
    skip: Optional[set[str]] = None,
    parallel: Optional[int] = None,
) -> BuildResult:
    """Apply `spec` to `original_rom` and write to `out_path`.

    `source_root` is the directory file= attrs are resolved against (typically the
    directory of the .mbxml file, optionally combined with spec.path).
    `original_rom` defaults to `spec.original` resolved relative to `source_root`.

    `only` / `skip` filter sections by `kind` (e.g. {"asar","script"}).
    Filtered-out sections land in `BuildResult.skipped` alongside `if=`-skipped
    ones. Post-process steps (revbyte, pad, checksum, diff) always run.

    `parallel` enables CPU-bound pre-encoding via `ProcessPoolExecutor`:
    `None` skips parallel prep entirely (default — zero overhead for small
    builds), `1` runs the prepare phase serially (debugging), any larger
    integer caps the worker count (default `os.cpu_count()`).
    """
    t0 = perf_counter()

    # A <libsfx> section generates the ROM canvas itself, so `original` is
    # optional when one is present (it will be replaced on first dispatch).
    has_libsfx = any(s.kind is SectionKind.LIBSFX for s in spec.sections)

    if original_rom is None and spec.original is not None:
        original_rom = (source_root / Path(str(spec.original))).resolve()
    if original_rom is None:
        if not has_libsfx:
            raise HandlerError("BuildSpec has no `original` and no `original_rom` was given")
        smc = None
        rom = bytearray()
    else:
        original_rom = Path(original_rom)
        if not original_rom.exists():
            raise HandlerError(f"original ROM not found: {original_rom}")
        raw = original_rom.read_bytes()
        smc, body = _strip_smc_header(raw)
        rom = bytearray(body)
        # Pre-expand ROM to cover declared freespace using pad_byte, so gap fill
        # between source tail and first section write matches project expectations.
        if spec.freespace:
            hi_max = max(hi for _, hi in spec.freespace)
            if hi_max > len(rom):
                rom.extend(bytes([spec.pad_byte & 0xFF]) * (hi_max - len(rom)))

    # Resolve build-files root: source_root + spec.path (if provided).
    files_root = source_root
    if spec.path is not None:
        files_root = (source_root / Path(str(spec.path))).resolve()

    if parallel is not None:
        from retrotool.build.prepare import parallel_prepare
        parallel_prepare(spec, files_root, max_workers=parallel)

    ctx = BuildContext(
        allocator=FreespaceAllocator.from_pairs(list(spec.freespace)) if spec.freespace else None,
        labels=dict(spec.labels) if hasattr(spec, "labels") and spec.labels else {},
    )

    # Inherit project-level [rom.build.section.placement] into inline script
    # sections that didn't declare their own. DataDef-derived sections already
    # inherit via resolve._section_from_datadef.
    _default_placement = (spec.section_defaults or {}).get("placement")
    if _default_placement:
        for _sec in spec.sections:
            if _sec.kind is SectionKind.SCRIPT and not _sec.placement:
                _sec.placement = dict(_default_placement)

    section_results: list[SectionResult] = []
    skipped: list[Section] = []
    cache_hits = 0
    keep_kind = _section_kinds_filter(only, skip)

    def _section_is_kept(section: Section) -> bool:
        if keep_kind is not None and not keep_kind(section):
            return False
        if section.condition is not None and not evaluate_condition(
            section.condition, spec.vars, source=section.source or "",
        ):
            return False
        return True

    # Pre-pass: reserve freespace for every cached section we will replay.
    # Cache stores absolute PCs baked by a prior allocator pass; a fresh
    # alloc() for a non-cached section processed earlier in iteration order
    # would otherwise hand out the same bytes. Reserving up-front makes the
    # fix order-independent.
    if cache and ctx.allocator is not None:
        for section in spec.sections:
            if not _section_is_kept(section):
                continue
            cache_key = _section_cache_key(section, files_root)
            if not cache_key or not cache.has(cache_key):
                continue
            try:
                entry = cache.get(cache_key)
                blob = entry.artifact.read_bytes()
                ranges = _unpack_writes(blob)
            except (OSError, AttributeError, ValueError):
                continue  # main loop will raise with a clearer error
            for off, data in ranges:
                ctx.allocator.reserve(off, len(data))

    for section in spec.sections:
        if not _section_is_kept(section):
            skipped.append(section)
            continue
        handler = get_handler(section.kind)
        if handler is None:
            raise HandlerError(
                f"{section.source}: no handler for <{section.kind.value}> "
                f"(landing in a later phase)"
            )

        cache_key = _section_cache_key(section, files_root) if cache else None
        if cache and cache_key and cache.has(cache_key):
            try:
                entry = cache.get(cache_key)
                blob = entry.artifact.read_bytes()
                ranges = _unpack_writes(blob)
            except (OSError, AttributeError, ValueError) as exc:
                raise HandlerError(
                    f"{section.source}: cached artifact unreadable for key "
                    f"{cache_key[:12]}…: {exc}"
                ) from exc
            cached_writes: list[WriteRange] = []
            for off, data in ranges:
                end = off + len(data)
                if end > len(rom):
                    rom.extend(b"\x00" * (end - len(rom)))
                rom[off:end] = data
                cached_writes.append(WriteRange(offset=off, length=len(data)))
            section_results.append(SectionResult(
                section=section, write=cached_writes, cache_hit=True,
            ))
            cache_hits += 1
            continue

        raw = handler(rom, section, files_root, ctx)
        writes: list[WriteRange] = [raw] if isinstance(raw, WriteRange) else list(raw)
        section_results.append(SectionResult(section=section, write=writes))
        # `export-label` registers a global label at the section's first write.
        export_name = section.attrs.get("export-label")
        if export_name and writes:
            ctx.labels[export_name] = writes[0].offset
        if cache and cache_key:
            cache.put(cache_key, _pack_writes(bytes(rom), writes),
                      meta={"kind": section.kind.value, "source": section.source or ""})

    # Revision byte patch. Convention: plain digits parse as decimal; anything
    # else is treated as hex (with optional `0x`/`$` prefix stripped).
    if spec.revbyteloc is not None and spec.revision is not None:
        rev_str = spec.revision.strip()
        if rev_str.isdigit():
            rev_byte = int(rev_str)
        else:
            rev_byte = int(rev_str.removeprefix("0x").removeprefix("$"), 16)
        rom[spec.revbyteloc] = rev_byte & 0xFF

    if spec.pad:
        _pad_to_next_size(rom, spec.pad_byte)

    csum = _patch_checksum(rom)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if smc is not None:
        out_path.write_bytes(smc + bytes(rom))
    else:
        out_path.write_bytes(bytes(rom))

    # Mesen2 SRAM sync (post-ROM-write). Copies the source ROM's .srm
    # to the output ROM's .srm so an in-progress save state transfers
    # across builds. Refuses to clobber the source .srm (would happen if
    # source and output share a stem). Silent no-op when source .srm
    # doesn't exist.
    if spec.sync_sram and original_rom is not None:
        from retrotool.debugger.mesen_saves import resolve_saves_dir, sync_sram
        saves = resolve_saves_dir(spec.mesen_saves_dir)
        sync_sram(original_rom, out_path, saves_dir=saves,
                  archive=spec.archive_sram)

    # Diff output (post-ROM-write). `spec.diff` may be "ips", "xdelta", or
    # "both" (comma-separated values accepted as well).
    diffs: list[DiffResult] = []
    if spec.diff and original_rom is not None:
        formats = [f.strip().lower() for f in spec.diff.replace("+", ",").split(",") if f.strip()]
        if formats == ["both"]:
            formats = ["ips", "xdelta"]
        for fmt in formats:
            diffs.append(write_diff(
                fmt, original_path=original_rom, modified_path=out_path,
            ))

    return BuildResult(
        rom_path=out_path,
        rom_size=len(rom),
        sections=section_results,
        skipped=skipped,
        checksum=csum,
        duration_ms=int((perf_counter() - t0) * 1000),
        diffs=diffs,
        cache_hits=cache_hits,
    )

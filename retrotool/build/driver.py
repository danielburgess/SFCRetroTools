"""Build pipeline: BuildSpec → output ROM file.

Two-phase execution:
  1. **Gather (concurrent).** Parallel-eligible sections (handlers that don't
     read working-rom state, don't touch `ctx.allocator`, don't write
     `ctx.labels`) are dispatched to a `ThreadPoolExecutor`. Each worker runs
     the handler against a private bytearray scratch (a copy of the original
     ROM) and packages a `(WriteRange, bytes)` write-set. Output bytes are
     buffered, never applied directly.
  2. **Apply (serial, in declared order).** The main loop walks
     `spec.sections`. Cache-hit / parallel / serial sections are applied or
     run in declared order against the shared `rom` bytearray. Order is
     preserved so `export-label` exports, freespace allocation, and `<asar>`
     patches see prior writes exactly as the legacy serial flow did.

Post-process — revbyteloc patch, pad to next SNES size, fix checksum,
optional diff (xdelta/IPS) — runs serially after the section loop.

Progress is reported through an optional `Reporter`; see `reporter.py`.
"""
from __future__ import annotations

import os
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Callable, Optional

from retrotool.core.cache import BuildCache, sha256_file, sha256_many
from retrotool.core.rom import _strip_smc_header, detect_header
from retrotool.build.diff import DiffResult, write_diff
from retrotool.build.handlers import BuildContext, HandlerError, WriteRange, get_handler
from retrotool.build.overflow import FreespaceAllocator
from retrotool.build.interpolate import evaluate_condition
from retrotool.build.reporter import Reporter, SectionStatus
from retrotool.build.script_filter import ScriptFilter
from retrotool.build.spec import BuildSpec, Section, SectionKind


# Section kinds whose handler is safe to run in a worker thread:
#   * never calls `ctx.allocator.alloc/reserve` (no shared mutable state)
#   * never writes `ctx.labels` (`export-label` is applied by the driver
#     post-handler, so workers don't touch the shared label dict)
#   * either doesn't read `rom`, or reads it from a private scratch that
#     was snapshotted at submit time (so reads see prior serial writes
#     and the spec's per-section offsets don't overlap each other)
# `fixed_records` joins the set: its only rom read is the stride-padding
# preservation slice at `rom[offset:offset+stride*count]`. The worker
# scratch is bytearray(rom-at-submit-time) so the slice resolves to the
# same bytes the serial handler would have read, provided no other section
# writes into that region — which a well-formed spec doesn't do anyway.
_PARALLEL_KINDS = frozenset({
    SectionKind.REP, SectionKind.INS, SectionKind.BIN,
    SectionKind.GRAPHICS, SectionKind.FIXED_RECORDS,
})

# Script kinds whose heavy encode phase (file I/O + table-driven encoding)
# runs in a worker via `script_prepare`; the apply phase stays serial because
# placement still touches `ctx.allocator` and reads `ctx.labels`. The worker
# returns a `_PreparedScript` payload that the apply phase passes back into
# `handle_script` so the placement/fixup-resolve loop can run with the
# correct (fully-populated) shared state.
_SCRIPT_PARALLEL_KINDS = frozenset({
    SectionKind.SCRIPT, SectionKind.WINDOWED_SCRIPT,
})


def _is_parallel_eligible(section: Section) -> bool:
    """True when `section` can be dispatched to the gather ThreadPool.

    Beyond `_PARALLEL_KINDS`, ASAR sections opted in via `cache="1"` qualify:
    diff-mode `handle_asar` returns a `list[WriteRange]` covering only the
    bytes the patch actually changed, with no implicit dependency on later
    sections' writes. The worker mutates its scratch fully (the handler
    does `rom[:] = new_rom`); we then capture the bytes at the diff offsets
    and apply them to the real rom in declared order.

    Default-cache ASAR (no `cache="1"`) stays serial — its return value is
    a single full-rom WriteRange that, in parallel, would clobber any prior
    section's writes that were applied between submit and apply time.
    `<libsfx>` and `<project>` are likewise inherently serial: they replace
    or recursively rebuild the rom canvas.
    """
    if section.kind in _PARALLEL_KINDS:
        return True
    if section.kind in (SectionKind.ASAR, SectionKind.BASS) and section.cache:
        return True
    return False


def _is_script_parallel_eligible(section: Section) -> bool:
    """True when `section` is a script kind whose encode phase is worth
    pushing to a worker. Placement still happens serially via `_apply_gathered`
    (see the script-prepare worker pathway in `build()`)."""
    return section.kind in _SCRIPT_PARALLEL_KINDS


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
    # ca65 + ld65 are pure for a given (sources, includes, config, defines,
    # cpu, debug, toolchain version) tuple — the linker output doesn't depend
    # on prior-section ROM bytes (handler memcopies the linker output into
    # the working ROM at `offset`). Cache by default; opt out per-section
    # with cache="0" if a contributor uses ld65 features that read external
    # state we don't track.
    SectionKind.CA65,
})

# Kinds eligible for explicit opt-in (cache="1"). These need extra work to
# cache safely — see handle_asar's diff-mode path for the asar story.
# bass shares the same diff-mode wrapper (`_wrap_assembler_writes`), so
# the same opt-in semantics apply.
_OPT_IN_CACHEABLE_KINDS = frozenset({
    SectionKind.ASAR,
    SectionKind.BASS,
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
    # Assembler opt-in: walk transitive include/incbin deps and hash every
    # file. The entry file is already hashed via `section.files` above, but
    # deps aren't — this adds them. Also includes/defines/etc. are part of
    # the key. asar and bass share the structure; the dialect picks the
    # scanner and the attr keys we pin.
    if section.kind in (SectionKind.ASAR, SectionKind.BASS) and section.cache:
        from retrotool.build.asar_deps import scan_bass_deps, scan_deps
        raw = section.attrs
        if section.kind == SectionKind.ASAR:
            scanner = scan_deps
            tag = "asar"
            extra_attrs = ("includes", "defines", "allow-shrink")
        else:
            scanner = scan_bass_deps
            tag = "bass"
            extra_attrs = (
                "includes", "defines", "constants",
                "strict", "bass-cmd", "allow-shrink",
            )
        include_dirs: list[Path] = []
        for p in (raw.get("includes") or "").split("|"):
            if p:
                include_dirs.append((files_root / Path(p)).resolve())
        for fspec in section.files:
            entry = (files_root / Path(str(fspec))).resolve()
            if not entry.exists():
                continue
            for dep in scanner(entry, include_dirs=include_dirs):
                # `entry` itself is re-hashed here — cheap and keeps the
                # scanner self-contained (the caller doesn't need to skip
                # deps[0]).
                parts.append(f"{tag}_dep={dep}".encode())
                parts.append(sha256_file(dep).encode())
        # Pin the other handler-visible attrs explicitly so trailing attr
        # additions don't accidentally collide or invalidate.
        for k in extra_attrs:
            parts.append(f"{tag}_attr_{k}={raw.get(k, '')}".encode())

    # ca65 sections are deterministic on (sources, includes, config,
    # defines, cpu, debug, toolchain version). Hash each input piece so
    # editing any of them invalidates the cache.
    if section.kind == SectionKind.CA65:
        raw = section.attrs
        # Linker config — required by the handler; if missing the cache
        # still works because the handler raises before any write.
        cfg = raw.get("config")
        if cfg:
            cfg_path = (files_root / Path(cfg)).resolve()
            if cfg_path.exists():
                parts.append(b"ca65_config=" + str(cfg_path).encode())
                parts.append(sha256_file(cfg_path).encode())
        # Multi-source list (additive to section.files).
        extra = (raw.get("files") or "").strip()
        if extra:
            for f in extra.split("|"):
                f = f.strip()
                if not f:
                    continue
                p = (files_root / Path(f)).resolve()
                if p.exists():
                    parts.append(b"ca65_extra=" + str(p).encode())
                    parts.append(sha256_file(p).encode())
        # Transitive .include / .import scan against the entry source
        # uses retrotool.asm.ca65._scan_includes, but that function only
        # returns top-level includes (one level deep) — sufficient for
        # our cache invalidation purposes since libSFX-style projects pin
        # their include tree via the bundled wheel.
        try:
            from retrotool.asm.ca65 import _scan_includes  # type: ignore[attr-defined]
        except ImportError:
            _scan_includes = None  # type: ignore[assignment]
        include_dirs: list[Path] = []
        for p in (raw.get("includes") or "").split("|"):
            if p:
                include_dirs.append((files_root / Path(p)).resolve())
        if _scan_includes is not None:
            for fspec in section.files:
                entry = (files_root / Path(str(fspec))).resolve()
                if not entry.exists():
                    continue
                for inc in _scan_includes(entry, include_dirs):
                    parts.append(b"ca65_inc=" + str(inc).encode())
                    if inc.exists():
                        parts.append(sha256_file(inc).encode())
        # Pin every handler-visible attr so trailing attr additions don't
        # collide silently with existing cache entries.
        for k in (
            "files", "config", "offset", "length", "pad-byte",
            "includes", "defines", "cpu", "debug",
            "lib-paths", "cfg-paths", "allow-truncate", "sym",
        ):
            parts.append(f"ca65_attr_{k}={raw.get(k, '')}".encode())
        # Toolchain version pin — invalidate the cache when the bundled
        # ca65/ld65 is upgraded. Best-effort: skip silently if the wheel
        # isn't installed (the handler will fail at apply time anyway).
        try:
            from retrotool import _toolchain
            parts.append(
                f"ca65_ver={_toolchain.tool_version('ca65')}".encode()
            )
            parts.append(
                f"ld65_ver={_toolchain.tool_version('ld65')}".encode()
            )
        except Exception:  # noqa: BLE001 — toolchain absent is fine here
            pass

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


def _section_label(section: Section) -> str:
    """Short human-readable identifier for progress UI.

    Prefers DataDef name → user-set alias/name → first input file → source
    locator. Falls back to the kind string if nothing else is available.
    """
    if section.from_datadef:
        return section.from_datadef
    for key in ("name", "alias"):
        v = section.attrs.get(key)
        if isinstance(v, str) and v:
            return v
    if section.files:
        return Path(str(section.files[0])).name
    if section.source:
        return section.source.split(":")[-1]
    return section.kind.value


def section_ids_for_filter(section: Section) -> set[str]:
    """All lowercase identifiers that the section presents to `--only` /
    `--skip` and the script filter. Mirrors the matchable forms documented
    on `_section_kinds_filter`: kind, datadef name, attr name/alias, source
    locator and its `:`-suffix, plus the `section[N]` positional alias.
    """
    ids: set[str] = {section.kind.value.lower()}
    if section.from_datadef:
        ids.add(section.from_datadef.lower())
    for key in ("name", "alias"):
        v = section.attrs.get(key)
        if isinstance(v, str) and v:
            ids.add(v.lower())
    if section.source:
        src = section.source.lower()
        ids.add(src)
        if ":" in src:
            suffix = src.split(":", 1)[1]
            ids.add(suffix)
            if suffix.startswith("sections[") and suffix.endswith("]"):
                ids.add("section[" + suffix[len("sections["):])
    return ids


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

    def keep(section: Section) -> bool:
        ids = section_ids_for_filter(section)
        if only_l and ids.isdisjoint(only_l):
            return False
        if skip_l and not ids.isdisjoint(skip_l):
            return False
        return True

    return keep


@dataclass
class _GatherResult:
    """Output of a parallel-gather worker. Two shapes:

    * Pure-write kinds (REP/INS/BIN/GRAPHICS/FIXED_RECORDS, opt-in ASAR):
      `writes` + `data` (one bytes payload per WriteRange, captured from the
      worker's private scratch). Applied via memcpy in declared section order.

    * Script kinds (SCRIPT/WINDOWED_SCRIPT): `prepared` is the encoded
      payload from `script_prepare`. The apply phase invokes `handle_script`
      against the live rom with `prepared=` so placement/fixup-resolve sees
      the fully-populated `ctx.allocator` and `ctx.labels`.
    """
    writes: list[WriteRange] = field(default_factory=list)
    data: list[bytes] = field(default_factory=list)
    prepared: Optional[object] = None  # _PreparedScript when set


def _gather_parallel(
    section: Section, files_root: Path, scratch: bytearray,
) -> _GatherResult:
    """Worker entry: run a parallel-eligible handler against `scratch`.

    `scratch` is a per-worker private copy of the original ROM (post-libsfx,
    pre-section-writes). Pure-write handlers (`_PARALLEL_KINDS`) only call
    `_write` against it, so they neither observe nor produce shared state.
    Returns the WriteRange list emitted by the handler plus the bytes that
    landed in `scratch` for each range — those are what the caller will
    apply to the real rom in declared order."""
    handler = get_handler(section.kind)
    if handler is None:
        raise HandlerError(
            f"{section.source}: no handler for <{section.kind.value}>"
        )
    raw = handler(scratch, section, files_root, None)
    writes = [raw] if isinstance(raw, WriteRange) else list(raw)
    data = [bytes(scratch[w.offset:w.end]) for w in writes]
    return _GatherResult(writes=writes, data=data)


def _gather_script_prepare(
    section: Section, files_root: Path, scratch: bytearray,
    script_filter: Optional[ScriptFilter] = None,
) -> _GatherResult:
    """Worker entry: run `script_prepare` against a rom snapshot.

    No allocator, no labels — those are touched only by the apply phase.
    Returns a `_GatherResult` carrying the `_PreparedScript` payload; the
    apply phase invokes `handle_script` against the live rom with the
    prepared payload."""
    from retrotool.build.handlers import script_prepare as _script_prepare
    prepared = _script_prepare(
        bytes(scratch), section, files_root,
        script_filter=script_filter,
    )
    return _GatherResult(prepared=prepared)


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
    reporter: Optional[Reporter] = None,
    script_filter: Optional[ScriptFilter] = None,
) -> BuildResult:
    """Apply `spec` to `original_rom` and write to `out_path`.

    `source_root` is the directory file= attrs are resolved against (typically
    the directory of the .mbxml file, optionally combined with spec.path).
    `original_rom` defaults to `spec.original` resolved relative to
    `source_root`.

    `only` / `skip` filter sections by `kind` (e.g. {"asar","script"}).
    Filtered-out sections land in `BuildResult.skipped` alongside `if=`-skipped
    ones. Post-process steps (revbyte, pad, checksum, diff) always run.

    `parallel` controls the gather-phase ThreadPoolExecutor used for
    parallel-eligible section kinds (`_PARALLEL_KINDS`):
      * `None` (default) → fully serial (1 worker, no pool created). After
        the M0.5-M0.8 Python-side optimizations, build times for typical
        SFC projects are sub-second and parallel coordination overhead
        slightly exceeds the gain. Opt in to parallel when warranted.
      * `0` → `os.cpu_count()` workers (auto).
      * `1` → fully serial (explicit; equivalent to `None`).
      * `N>1` → cap workers at `N`.
    Determinism is preserved regardless: writes are applied in declared
    section order against the single working ROM, so output bytes are
    identical to the serial path.

    `reporter` receives lifecycle events for every section (queued, gather
    started, terminal). Pass `retrotool.build.reporter.make_reporter()` for
    a TTY-aware default; pass `None` for a silent build."""
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

    # Populate Section.address_type from spec mapping. The extract path does
    # this in extract.py; build needs the same so handlers don't fall back to
    # LoROM1 when the project is HiROM/SA-1/etc. Matches the rule documented
    # at extract.py:464.
    _spec_addr_type = spec.address_type()
    for _sec in spec.sections:
        if _sec.address_type is None:
            _sec.address_type = _spec_addr_type

    section_results: list[SectionResult] = []
    skipped: list[Section] = []
    cache_hits = 0
    keep_kind = _section_kinds_filter(only, skip)
    # A non-empty script_filter mutates handler output for the matched
    # sections without changing inputs the cache key sees. Forcing a
    # cache miss here is simpler and safer than embedding filter state
    # into the key (the filter changes per CLI invocation; persisted
    # entries would churn).
    _filter_active = script_filter is not None and not script_filter.is_empty()

    def _section_is_kept(section: Section) -> bool:
        if keep_kind is not None and not keep_kind(section):
            return False
        if section.condition is not None and not evaluate_condition(
            section.condition, spec.vars, source=section.source or "",
        ):
            return False
        return True

    # Resolve cache state per section once: avoids re-hashing input files in
    # the freespace pre-pass and the apply loop.
    section_cache_keys: dict[int, str] = {}
    cache_hit_set: set[int] = set()
    if cache:
        for i, section in enumerate(spec.sections):
            if not _section_is_kept(section):
                continue
            # Script sections produce filter-dependent output; bypass cache
            # when the filter is active so step-mode iterations don't all
            # collapse to the first build's cached writes.
            if _filter_active and section.kind in (
                SectionKind.SCRIPT, SectionKind.WINDOWED_SCRIPT,
            ):
                continue
            key = _section_cache_key(section, files_root)
            if key:
                section_cache_keys[i] = key
                if cache.has(key):
                    cache_hit_set.add(i)

    # Pre-pass: reserve freespace for every cached section we will replay.
    # Cache stores absolute PCs baked by a prior allocator pass; a fresh
    # alloc() for a non-cached section processed earlier in iteration order
    # would otherwise hand out the same bytes. Reserving up-front makes the
    # fix order-independent.
    if cache and ctx.allocator is not None:
        for i in sorted(cache_hit_set):
            try:
                entry = cache.get(section_cache_keys[i])
                blob = entry.artifact.read_bytes()
                ranges = _unpack_writes(blob)
            except (OSError, AttributeError, ValueError):
                continue  # apply loop will raise with a clearer error
            for off, data in ranges:
                ctx.allocator.reserve(off, len(data))

    # Notify reporter about every section so the progress UI can lay out rows
    # in declared order.
    if reporter is not None:
        reporter.build_started(len(spec.sections))
        for i, section in enumerate(spec.sections):
            reporter.section_queued(i, _section_label(section), section.kind.value)

    # Single-pass interleaved gather + apply. Parallel-eligible sections are
    # submitted at their declared index against a snapshot of `rom` AS OF
    # that moment (so any prior serial section's writes — libsfx canvas,
    # asar patches — are visible in the worker's scratch). When a serial
    # section is reached, we drain pending parallel futures whose declared
    # index precedes it and apply their writes first; the serial handler
    # then runs against the post-drain rom, matching legacy ordering.
    futures: dict[int, Future[_GatherResult]] = {}
    pool: Optional[ThreadPoolExecutor] = None
    # Resolution: None → serial (1). 0 → cpu_count (auto). N>0 → N workers.
    # Default-serial reflects post-optimization reality: at sub-second build
    # times the worker-coordination overhead exceeds the parallel gain.
    if parallel is None:
        max_workers = 1
    elif parallel == 0:
        max_workers = os.cpu_count() or 1
    else:
        max_workers = max(1, parallel)
    if max_workers and max_workers > 1:
        pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="retrotool-gather",
        )

    def _submit(idx: int, section: Section) -> None:
        # Snapshot rom AT SUBMISSION TIME — captures all preceding serial
        # writes so the worker scratch matches what serial execution would
        # have observed. Pure-write handlers don't read rom, but they do
        # call `_write(allow_grow=False)` which checks `len(scratch)`; an
        # empty pre-libsfx snapshot would fail any rep at a high offset.
        # Script workers read the snapshot to populate `_PreparedScript.
        # source_snapshot` (used by overflow placement and `slot-measure
        # =source-entry`) but do NOT touch the live rom or shared state.
        scratch = bytearray(rom)
        is_script_parallel = _is_script_parallel_eligible(section)

        # Fire GATHER from inside the worker (i.e. when a pool thread actually
        # picks the task up) rather than at submit-time. Otherwise the
        # reporter's per-section timer starts at queue-time and elapsed_ms
        # conflates queue-wait with actual work time. Fire GATHER_DONE in
        # `finally` so the reporter can drop the section out of the live
        # "active" region the moment the worker returns — without it, finished
        # tasks pile up visibly until the main thread eventually drains them.
        def _run() -> _GatherResult:
            if reporter is not None:
                reporter.section_status(idx, SectionStatus.GATHER)
            try:
                if is_script_parallel:
                    return _gather_script_prepare(
                        section, files_root, scratch,
                        script_filter=script_filter,
                    )
                return _gather_parallel(section, files_root, scratch)
            finally:
                if reporter is not None:
                    reporter.section_status(idx, SectionStatus.GATHER_DONE)

        if pool is None:
            fut: Future[_GatherResult] = Future()
            try:
                fut.set_result(_run())
            except Exception as exc:  # noqa: BLE001
                fut.set_exception(exc)
            futures[idx] = fut
            return
        futures[idx] = pool.submit(_run)

    applied: set[int] = set()

    def _apply_gathered(idx: int, section: Section) -> None:
        """Wait on `futures[idx]`, apply its result to `rom`, fire DONE.

        Two result shapes:
          * Pure-write (REP/INS/BIN/GRAPHICS/FIXED_RECORDS, opt-in ASAR):
            memcpy `gathered.data` into `rom` at `gathered.writes` offsets.
          * Script-prepared: invoke the handler against the live rom with
            `prepared=gathered.prepared` so placement/fixup-resolve sees
            the fully-populated `ctx.allocator` and `ctx.labels`.
        """
        try:
            gathered = futures[idx].result()
        except Exception as exc:  # noqa: BLE001
            if reporter is not None:
                reporter.section_status(idx, SectionStatus.ERROR, note=str(exc))
            raise
        if reporter is not None:
            reporter.section_status(idx, SectionStatus.APPLY)
        bytes_written = 0

        if _is_script_parallel_eligible(section):
            # Script worker — invoke handler against live rom with the
            # prepared payload (may be None when `script_prepare` declined,
            # e.g. legacy concat mode; the handler then encodes inline).
            handler = get_handler(section.kind)
            if handler is None:
                raise HandlerError(
                    f"{section.source}: no handler for <{section.kind.value}>"
                )
            try:
                raw = handler(
                    rom, section, files_root, ctx,
                    prepared=gathered.prepared,
                    script_filter=script_filter,
                )
            except Exception as exc:  # noqa: BLE001
                if reporter is not None:
                    reporter.section_status(
                        idx, SectionStatus.ERROR, note=str(exc),
                    )
                raise
            writes = [raw] if isinstance(raw, WriteRange) else list(raw)
            bytes_written = sum(w.length for w in writes)
        else:
            writes = gathered.writes
            for wr, data in zip(gathered.writes, gathered.data):
                end = wr.offset + len(data)
                if end > len(rom):
                    rom.extend(b"\x00" * (end - len(rom)))
                rom[wr.offset:end] = data
                bytes_written += len(data)

        section_results.append(SectionResult(section=section, write=writes))
        export_name = section.attrs.get("export-label")
        if export_name and writes:
            ctx.labels[export_name] = writes[0].offset
        if cache and idx in section_cache_keys:
            cache.put(
                section_cache_keys[idx],
                _pack_writes(bytes(rom), writes),
                meta={"kind": section.kind.value,
                      "source": section.source or ""},
            )
        applied.add(idx)
        if reporter is not None:
            reporter.section_status(
                idx, SectionStatus.DONE, bytes_written=bytes_written,
            )

    def _drain_through(upto_idx: int) -> None:
        """Apply any pending parallel-gather results with index < upto_idx,
        in declared order. Called before any serial-section work so that
        serial handlers (asar, script, etc.) observe prior parallel writes."""
        for j in sorted(j for j in futures if j < upto_idx and j not in applied):
            _apply_gathered(j, spec.sections[j])

    try:
        for i, section in enumerate(spec.sections):
            if not _section_is_kept(section):
                skipped.append(section)
                if reporter is not None:
                    reason = "filtered" if keep_kind and not keep_kind(section) else "condition"
                    reporter.section_status(i, SectionStatus.SKIPPED, note=reason)
                continue

            handler = get_handler(section.kind)
            if handler is None:
                raise HandlerError(
                    f"{section.source}: no handler for <{section.kind.value}> "
                    f"(landing in a later phase)"
                )

            # Cache hit — apply stored writes directly. (Cached sections
            # don't need a snapshot; their bytes are already known.)
            if i in cache_hit_set:
                # Drain any earlier parallel work first so writes apply in
                # declared order — a later cache-hit section that overlaps
                # an earlier parallel write would otherwise lose data.
                _drain_through(i)
                key = section_cache_keys[i]
                try:
                    entry = cache.get(key)
                    blob = entry.artifact.read_bytes()
                    ranges = _unpack_writes(blob)
                except (OSError, AttributeError, ValueError) as exc:
                    raise HandlerError(
                        f"{section.source}: cached artifact unreadable for "
                        f"key {key[:12]}…: {exc}"
                    ) from exc
                cached_writes: list[WriteRange] = []
                bytes_written = 0
                for off, data in ranges:
                    end = off + len(data)
                    if end > len(rom):
                        rom.extend(b"\x00" * (end - len(rom)))
                    rom[off:end] = data
                    cached_writes.append(WriteRange(offset=off, length=len(data)))
                    bytes_written += len(data)
                section_results.append(SectionResult(
                    section=section, write=cached_writes, cache_hit=True,
                ))
                cache_hits += 1
                if reporter is not None:
                    reporter.section_status(
                        i, SectionStatus.CACHE_HIT, bytes_written=bytes_written,
                    )
                continue

            # Parallel-eligible: snapshot rom NOW, dispatch worker, continue.
            # We don't apply yet — apply happens when a later serial section
            # drains us, or at end-of-loop. Script-parallel sections push only
            # the encode phase to the worker; placement (which calls
            # ctx.allocator and reads ctx.labels) runs serially in apply.
            if _is_parallel_eligible(section) or _is_script_parallel_eligible(section):
                _submit(i, section)
                continue

            # Serial path — asar / libsfx / project / fixed_records.
            # Drain prior parallel work first so the handler observes those
            # writes, then run against the shared rom.
            _drain_through(i)
            if reporter is not None:
                reporter.section_status(i, SectionStatus.GATHER)
            try:
                raw = handler(rom, section, files_root, ctx)
            except Exception as exc:  # noqa: BLE001
                if reporter is not None:
                    reporter.section_status(
                        i, SectionStatus.ERROR, note=str(exc),
                    )
                raise
            writes = [raw] if isinstance(raw, WriteRange) else list(raw)
            bytes_written = sum(w.length for w in writes)
            section_results.append(SectionResult(section=section, write=writes))
            export_name = section.attrs.get("export-label")
            if export_name and writes:
                ctx.labels[export_name] = writes[0].offset
            if cache and i in section_cache_keys:
                cache.put(
                    section_cache_keys[i],
                    _pack_writes(bytes(rom), writes),
                    meta={"kind": section.kind.value,
                          "source": section.source or ""},
                )
            applied.add(i)
            if reporter is not None:
                reporter.section_status(
                    i, SectionStatus.DONE, bytes_written=bytes_written,
                )

        # End-of-loop drain: trailing parallel sections (no later serial
        # section to force a flush) apply now, in declared order.
        _drain_through(len(spec.sections))
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)

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

    result = BuildResult(
        rom_path=out_path,
        rom_size=len(rom),
        sections=section_results,
        skipped=skipped,
        checksum=csum,
        duration_ms=int((perf_counter() - t0) * 1000),
        diffs=diffs,
        cache_hits=cache_hits,
    )
    if reporter is not None:
        summary = (
            f"built {out_path.name} · {len(rom):,}b · "
            f"{len(section_results)} section(s) "
            f"({cache_hits} cached, {len(skipped)} skipped) · "
            f"{result.duration_ms} ms"
        )
        reporter.build_done(ok=True, summary=summary)
    return result

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
from retrotool.mbuild.diff import DiffResult, write_diff
from retrotool.mbuild.handlers import HandlerError, WriteRange, get_handler
from retrotool.mbuild.interpolate import evaluate_condition
from retrotool.mbuild.spec import BuildSpec, Section, SectionKind


# Section kinds with single-range writes (offset+length deterministic from
# attrs+inputs). <asar> and <project> can write anywhere in the ROM, so we
# don't cache them — the key wouldn't capture the output region reliably.
_CACHEABLE_KINDS = frozenset({
    SectionKind.REP, SectionKind.INS, SectionKind.BIN,
    SectionKind.GRAPHICS, SectionKind.SCRIPT,
})

# Bumped when the cache-key schema changes; old entries are ignored on read.
# v3: cache artifact reframed to support multi-range writes per section.
_CACHE_VERSION = "3"

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


def _section_cache_key(section: Section, files_root: Path) -> Optional[str]:
    """SHA-256 over section kind + attrs + input-file content hashes.

    Returns None if the section is in a non-cacheable kind or any input file
    is missing (handler will raise its own error)."""
    if section.kind not in _CACHEABLE_KINDS:
        return None
    parts: list[bytes] = [f"v{_CACHE_VERSION}".encode(), section.kind.value.encode()]
    # Stable attr ordering — we hash dict items, not the dict.
    for k in sorted(section.attrs):
        parts.append(f"{k}={section.attrs[k]}".encode())
    # `size` and `grow` are first-class Section fields, often absent from
    # `attrs` after TOML coercion. Hash them explicitly so two sections that
    # differ only in those fields don't collide.
    parts.append(f"__size__={section.size}".encode())
    parts.append(f"__grow__={section.grow}".encode())
    for f in section.files:
        path = (files_root / Path(str(f))).resolve()
        if not path.exists():
            return None
        parts.append(sha256_file(path).encode())
    if section.table is not None:
        tpath = (files_root / Path(str(section.table))).resolve()
        if tpath.exists():
            parts.append(sha256_file(tpath).encode())
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


def _pad_to_next_size(rom: bytearray) -> int:
    """Pad with 0x00 to the next valid SNES ROM size. Returns final size."""
    cur = len(rom)
    for size in _SNES_PAD_SIZES:
        if cur <= size:
            if size != cur:
                rom.extend(b"\x00" * (size - cur))
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
    """Return a predicate(section) → True if the section should run."""
    if not only and not skip:
        return None
    only_l = {s.lower() for s in (only or set())}
    skip_l = {s.lower() for s in (skip or set())}

    def keep(section: Section) -> bool:
        kind = section.kind.value.lower()
        if only_l and kind not in only_l:
            return False
        if kind in skip_l:
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

    # Resolve build-files root: source_root + spec.path (if provided).
    files_root = source_root
    if spec.path is not None:
        files_root = (source_root / Path(str(spec.path))).resolve()

    if parallel is not None:
        from retrotool.mbuild.prepare import parallel_prepare
        parallel_prepare(spec, files_root, max_workers=parallel)

    section_results: list[SectionResult] = []
    skipped: list[Section] = []
    cache_hits = 0
    keep_kind = _section_kinds_filter(only, skip)
    for section in spec.sections:
        if keep_kind is not None and not keep_kind(section):
            skipped.append(section)
            continue
        if section.condition is not None and not evaluate_condition(
            section.condition, spec.vars, source=section.source or "",
        ):
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

        raw = handler(rom, section, files_root)
        writes: list[WriteRange] = [raw] if isinstance(raw, WriteRange) else list(raw)
        section_results.append(SectionResult(section=section, write=writes))
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
        _pad_to_next_size(rom)

    csum = _patch_checksum(rom)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if smc is not None:
        out_path.write_bytes(smc + bytes(rom))
    else:
        out_path.write_bytes(bytes(rom))

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

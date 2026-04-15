"""Symmetric extract pipeline: ROM → files.

Mirrors `build.py`. Each `Section` is dispatched to an extract handler that
reads bytes from the working ROM and writes them under `dest_root`.

Sizing strategy for raw kinds (rep/ins/bin):
  1. `section.size` if set.
  2. Existing file at the resolved path (its byte length = read length).
     This is the round-trip case where extract overwrites previously-written files.
  3. For multi-file <ins file="A|B|C"/>, sum of existing files' sizes; if any
     are missing → error.
  4. Otherwise raise — we don't guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Callable, Optional

from retrotool.core.rom import _strip_smc_header
from retrotool.mbuild.handlers import HandlerError
from retrotool.mbuild.spec import BuildSpec, Section, SectionKind


@dataclass
class ExtractedSection:
    section: Section
    files: list[Path] = field(default_factory=list)
    bytes_read: int = 0


@dataclass
class ExtractResult:
    sections: list[ExtractedSection] = field(default_factory=list)
    duration_ms: int = 0


def _resolve(file: Path, root: Path) -> Path:
    p = Path(file)
    return p if p.is_absolute() else (root / p).resolve()


def _file_sizes(section: Section, root: Path) -> Optional[list[int]]:
    sizes: list[int] = []
    for f in section.files:
        path = _resolve(Path(str(f)), root)
        if not path.exists():
            return None
        sizes.append(path.stat().st_size)
    return sizes


def _resolve_total_size(section: Section, dest_root: Path) -> tuple[int, list[int]]:
    """Return (total_bytes_to_read, per_file_split). Raises if undeterminable."""
    if section.size is not None:
        # Single-file section with explicit size.
        if len(section.files) > 1:
            raise HandlerError(
                f"{section.source}: section.size set but file= has {len(section.files)} parts"
            )
        return section.size, [section.size]

    sizes = _file_sizes(section, dest_root)
    if sizes is None:
        raise HandlerError(
            f"{section.source}: cannot extract <{section.kind.value}> at {section.offset:#x}: "
            "no size= and target file(s) don't exist (size cannot be inferred)"
        )
    return sum(sizes), sizes


def _write_split(rom: bytes, section: Section, dest_root: Path,
                 splits: list[int]) -> list[Path]:
    """Read `sum(splits)` bytes from `section.offset`, write each chunk to its file."""
    assert section.offset is not None
    assert len(splits) == len(section.files)
    out_paths: list[Path] = []
    cursor = section.offset
    for f, n in zip(section.files, splits):
        path = _resolve(Path(str(f)), dest_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rom[cursor:cursor + n])
        out_paths.append(path)
        cursor += n
    return out_paths


# ---- handlers -------------------------------------------------------------

def extract_raw(rom: bytes, section: Section, dest_root: Path) -> ExtractedSection:
    """Shared raw extractor for rep / ins / bin.

    BIN with codec= reads from the ROM offset, decompresses (decoder reports how
    many compressed bytes it consumed), writes the decompressed payload to the
    single output file. Multi-file concat is not supported for compressed bins."""
    if section.offset is None:
        raise HandlerError(f"{section.source}: <{section.kind.value}> requires offset")
    if not section.files:
        raise HandlerError(f"{section.source}: <{section.kind.value}> requires file=")

    if section.kind is SectionKind.BIN and section.codec:
        if len(section.files) != 1:
            raise HandlerError(
                f"{section.source}: <bin codec=…> extract requires exactly one output file"
            )
        from retrotool.compression import registry as codec_registry
        try:
            codec = codec_registry.get(section.codec)
        except KeyError as e:
            raise HandlerError(
                f"{section.source}: unknown codec={section.codec!r}. "
                f"Known: {codec_registry.list_schemes()}"
            ) from e
        # Codecs without self-terminating markers (e.g. lzss-legacy, rle) will
        # keep reading past the compressed payload into ROM padding. If caller
        # declared size=, slice to that bound; else rely on codec to self-stop.
        if section.size is not None:
            buf = bytes(rom[section.offset:section.offset + section.size])
            result = codec.decompress(buf, offset=0)
        else:
            result = codec.decompress(bytes(rom), offset=section.offset)
        path = _resolve(Path(str(section.files[0])), dest_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(result.data)
        return ExtractedSection(section=section, files=[path], bytes_read=result.consumed)

    total, splits = _resolve_total_size(section, dest_root)
    if section.offset + total > len(rom):
        raise HandlerError(
            f"{section.source}: read of {total}b at {section.offset:#x} exceeds ROM size {len(rom):#x}"
        )
    paths = _write_split(rom, section, dest_root, splits)
    return ExtractedSection(section=section, files=paths, bytes_read=total)


def extract_graphics(rom: bytes, section: Section, dest_root: Path) -> ExtractedSection:
    """Mirror of handle_graphics: read ROM region, apply reverse bitplane
    transform, write to file. Phase 3 wires raw passthrough only."""
    from retrotool.mbuild.handlers import bitplane_reverse

    if section.offset is None:
        raise HandlerError(f"{section.source}: <graphics> requires offset")
    if not section.files:
        raise HandlerError(f"{section.source}: <graphics> requires file=")
    total, _ = _resolve_total_size(section, dest_root)
    if section.offset + total > len(rom):
        raise HandlerError(
            f"{section.source}: read of {total}b at {section.offset:#x} exceeds ROM size {len(rom):#x}"
        )
    reverse = bitplane_reverse(section.codec)
    raw = bytes(rom[section.offset:section.offset + total])
    decoded = reverse(raw)
    path = _resolve(Path(str(section.files[0])), dest_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(decoded)
    return ExtractedSection(section=section, files=[path], bytes_read=total)


def extract_script(rom: bytes, section: Section, dest_root: Path) -> ExtractedSection:
    """Mirror of `handle_script`: read $00-terminated entries until size exhausted,
    decode via Table, write one line per entry to the text file."""
    if section.offset is None:
        raise HandlerError(f"{section.source}: <script> requires offset")
    if section.table is None:
        raise HandlerError(f"{section.source}: <script> requires table=")
    if not section.files:
        raise HandlerError(f"{section.source}: <script> requires file=")

    # Need to know how many bytes to scan. Use size=, else existing file's
    # encoded length (re-encode round-trip), else error.
    total: Optional[int] = section.size
    if total is None:
        total, _ = _resolve_total_size(section, dest_root)

    from retrotool.script.table import Table  # deferred heavy import

    table = Table(_resolve(Path(str(section.table)), dest_root))
    region = rom[section.offset:section.offset + total]
    lines: list[str] = []
    buf = bytearray()
    for b in region:
        if b == 0x00:
            lines.append(_decode_bytes(table, bytes(buf)))
            buf.clear()
        else:
            buf.append(b)
    if buf:
        lines.append(_decode_bytes(table, bytes(buf)))

    text_path = _resolve(Path(str(section.files[0])), dest_root)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ExtractedSection(section=section, files=[text_path], bytes_read=total)


def _decode_bytes(table, data: bytes) -> str:
    """Greedy longest-match decode using Table.get_chars per byte."""
    out: list[str] = []
    i = 0
    n = len(data)
    while i < n:
        # Try longest token first (4 → 1 bytes).
        matched = False
        for width in (4, 3, 2, 1):
            if i + width > n:
                continue
            value = int.from_bytes(data[i:i + width], "big")
            chars = table.get_chars(value, return_hex_repr=False)
            if chars is not None:
                out.append(chars)
                i += width
                matched = True
                break
        if not matched:
            out.append(f"[{data[i]:02X}]")
            i += 1
    return "".join(out)


ExtractFn = Callable[[bytes, Section, Path], ExtractedSection]

def extract_fixed_records(rom: bytes, section: Section, dest_root: Path) -> ExtractedSection:
    """Mirror of `handle_fixed_records`: dump `stride * count` (or section.size,
    or existing file size) bytes from offset to a single file."""
    if section.offset is None:
        raise HandlerError(f"{section.source}: <fixed-records> requires offset")
    if not section.files:
        raise HandlerError(f"{section.source}: <fixed-records> requires file=")
    if section.stride is not None and section.count is not None:
        total = section.stride * section.count
    elif section.size is not None:
        total = section.size
    else:
        total, _ = _resolve_total_size(section, dest_root)
    if section.offset + total > len(rom):
        raise HandlerError(
            f"{section.source}: read of {total}b at {section.offset:#x} "
            f"exceeds ROM size {len(rom):#x}"
        )
    path = _resolve(Path(str(section.files[0])), dest_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(rom[section.offset:section.offset + total]))
    return ExtractedSection(section=section, files=[path], bytes_read=total)


EXTRACT_HANDLERS: dict[SectionKind, ExtractFn] = {
    SectionKind.REP: extract_raw,
    SectionKind.INS: extract_raw,
    SectionKind.BIN: extract_raw,
    SectionKind.GRAPHICS: extract_graphics,
    SectionKind.SCRIPT: extract_script,
    SectionKind.FIXED_RECORDS: extract_fixed_records,
}


def get_extract_handler(kind: SectionKind) -> Optional[ExtractFn]:
    return EXTRACT_HANDLERS.get(kind)


# ---- driver ---------------------------------------------------------------

def extract(
    spec: BuildSpec,
    *,
    source_root: Path,
    original_rom: Optional[Path] = None,
    dest_root: Optional[Path] = None,
    only: Optional[set[str]] = None,
    skip: Optional[set[str]] = None,
) -> ExtractResult:
    """Run extract for every supported section in `spec`.

    `source_root` resolves spec.original. `dest_root` resolves output file= attrs;
    defaults to `source_root + spec.path` (mirrors build()).

    `only` / `skip` filter sections by `kind`. Filtered-out sections are silently
    omitted from the result."""
    t0 = perf_counter()
    if original_rom is None:
        if spec.original is None:
            raise HandlerError("BuildSpec has no `original` and no `original_rom` was given")
        original_rom = (source_root / Path(str(spec.original))).resolve()
    original_rom = Path(original_rom)
    if not original_rom.exists():
        raise HandlerError(f"original ROM not found: {original_rom}")

    raw = original_rom.read_bytes()
    _, body = _strip_smc_header(raw)

    files_root = dest_root if dest_root is not None else source_root
    if spec.path is not None and dest_root is None:
        files_root = (source_root / Path(str(spec.path))).resolve()

    only_l = {s.lower() for s in (only or set())}
    skip_l = {s.lower() for s in (skip or set())}

    results: list[ExtractedSection] = []
    for section in spec.sections:
        kind = section.kind.value.lower()
        if only_l and kind not in only_l:
            continue
        if kind in skip_l:
            continue
        handler = get_extract_handler(section.kind)
        if handler is None:
            raise HandlerError(
                f"{section.source}: no extract handler for <{section.kind.value}> "
                "(landing in a later phase)"
            )
        results.append(handler(body, section, files_root))

    return ExtractResult(sections=results, duration_ms=int((perf_counter() - t0) * 1000))

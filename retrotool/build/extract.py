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
from retrotool.build.driver import _section_kinds_filter
from retrotool.build.handlers import HandlerError
from retrotool.build.spec import BuildSpec, Section, SectionKind


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
    from retrotool.build.handlers import bitplane_reverse

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
    """Mirror of `handle_script`. Two modes:

    - Pointer-table-driven (`pointer-table=` + `pointer-size=` + `count=`):
      read each ptr, decode the entry at its target PC up to the first
      $00 terminator (ctrl_lengths-aware so multi-byte ctrls aren't split),
      emit one `<<$TBL:IDX[$PC]>>` block per entry. Sentinel ptrs that
      decode outside the LoROM window produce empty entries — the build
      side carries the raw source ptr through verbatim.
    - Flat (`offset=`): legacy mode, read $00-terminated entries across
      the region.
    """
    if section.table is None:
        raise HandlerError(f"{section.source}: <script> requires table=")
    if not section.files:
        raise HandlerError(f"{section.source}: <script> requires file=")

    from retrotool.script.table import Table  # deferred heavy import

    table = Table(_resolve(Path(str(section.table)), dest_root))

    if section.pointer_table is not None:
        return _extract_script_pointer_table(rom, section, dest_root, table)

    if section.offset is None:
        raise HandlerError(
            f"{section.source}: <script> requires offset= (or pointer-table=)"
        )

    # Need to know how many bytes to scan. Use size=, else existing file's
    # encoded length (re-encode round-trip), else error.
    total: Optional[int] = section.size
    if total is None:
        total, _ = _resolve_total_size(section, dest_root)

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
    # UTF-16 LE w/ BOM matches LM3 convention, .tbl files, and the
    # encoder's BOM-detect path (`_read_script_text` in encode.py). Plain
    # UTF-8 causes char-map lookups against UTF-16 .tbl keys to miss,
    # falling back to per-byte hex emission and ballooning entries.
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-16")
    return ExtractedSection(section=section, files=[text_path], bytes_read=total)


def _extract_script_pointer_table(
    rom: bytes, section: Section, dest_root: Path, table,
) -> ExtractedSection:
    from retrotool.core.address import SFCAddress, SFCAddressType

    if section.count is None:
        raise HandlerError(f"{section.source}: <script pointer-table> requires count=")
    ptr_size = section.pointer_size or 2
    if ptr_size not in (2, 3):
        raise HandlerError(
            f"{section.source}: pointer-size must be 2 or 3, got {ptr_size}"
        )

    ptr_tbl_pc = section.pointer_table
    count = int(section.count)
    ptr_tbl_len = count * ptr_size
    if ptr_tbl_pc + ptr_tbl_len > len(rom):
        raise HandlerError(
            f"{section.source}: ptr table read of {ptr_tbl_len}b at "
            f"{ptr_tbl_pc:#x} exceeds ROM size {len(rom):#x}"
        )

    bank_hi = (
        SFCAddress(ptr_tbl_pc).get_bank_byte(SFCAddressType.LOROM1)
        if ptr_size == 2 else 0
    )
    ptr_bytes = bytes(rom[ptr_tbl_pc:ptr_tbl_pc + ptr_tbl_len])

    # First pass: decode every ptr → per-index PC (None for sentinels).
    pcs: list[Optional[int]] = []
    for i in range(count):
        raw = ptr_bytes[i * ptr_size:(i + 1) * ptr_size]
        if ptr_size == 3:
            snes = raw[0] | (raw[1] << 8) | (raw[2] << 16)
        else:
            snes = (bank_hi << 16) | raw[0] | (raw[1] << 8)
        pcs.append(SFCAddress.lorom1_to_pc(snes, verbose=False))

    # Entry boundaries come from the ptr table itself: each entry runs
    # from its PC up to the next unique PC (primary bound). Within that
    # window `find_entry_end` serves as a secondary early-stop for
    # 0x00-terminated entries whose real end is before the next ptr.
    # Without the primary bound, entries ending in FFC0 redirects (no
    # trailing 0x00) cause `find_entry_end` to walk into subsequent
    # entries' bytes — swallowing multiple entries into one.
    sorted_unique = sorted({pc for pc in pcs if pc is not None})
    next_bound: dict[int, int] = {}
    for idx, pc in enumerate(sorted_unique):
        next_bound[pc] = sorted_unique[idx + 1] if idx + 1 < len(sorted_unique) else len(rom)

    lines: list[str] = []
    bytes_read = ptr_tbl_len
    for i, pc in enumerate(pcs):
        if pc is None:
            # Sentinel — empty entry. Header omits [$PC] since no real PC.
            lines.append(f"<<${ptr_tbl_pc}:{i}>>")
            lines.append("")
            continue
        bound = next_bound[pc]
        soft_end = table.find_entry_end(rom, pc)
        end = min(soft_end, bound)
        decoded = table.interpret_binary_data(list(rom[pc:end]))
        lines.append(f"<<${ptr_tbl_pc}:{i}[${pc}]>>")
        lines.append(decoded)
        bytes_read += (end - pc)

    text_path = _resolve(Path(str(section.files[0])), dest_root)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    # UTF-16 LE w/ BOM matches LM3 convention, .tbl files, and the
    # encoder's BOM-detect path (`_read_script_text` in encode.py). Plain
    # UTF-8 causes char-map lookups against UTF-16 .tbl keys to miss,
    # falling back to per-byte hex emission and ballooning entries.
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-16")
    return ExtractedSection(section=section, files=[text_path], bytes_read=bytes_read)


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

def _planned_targets(section: Section, files_root: Path) -> list[Path]:
    """Paths an extract handler would write for this section (no side effects)."""
    out: list[Path] = []
    for f in section.files:
        out.append(_resolve(Path(str(f)), files_root))
    return out


def extract(
    spec: BuildSpec,
    *,
    source_root: Path,
    original_rom: Optional[Path] = None,
    dest_root: Optional[Path] = None,
    only: Optional[set[str]] = None,
    skip: Optional[set[str]] = None,
    confirm_existing: Optional[Callable[[list[Path]], bool]] = None,
) -> ExtractResult:
    """Run extract for every supported section in `spec`.

    `source_root` resolves spec.original. `dest_root` resolves output file= attrs;
    defaults to `source_root + spec.path` (mirrors build()).

    `only` / `skip` filter sections by kind, from_datadef, or source id.

    `confirm_existing` (optional): callable invoked with the list of target
    paths that already exist on disk. Return True to proceed with overwrites,
    False to abort (raises HandlerError). When None, existing files are
    overwritten silently — intended for programmatic callers that have their
    own guard. CLI installs an interactive prompt here."""
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

    # Mirror build(): files_root = base + spec.path (always), where base is
    # dest_root when given, else source_root.
    base = dest_root if dest_root is not None else source_root
    files_root = (base / Path(str(spec.path))).resolve() if spec.path is not None else base

    keep = _section_kinds_filter(only, skip)

    # Pre-pass: validate handlers + collect targets for overwrite check.
    planned: list[Section] = []
    existing: list[Path] = []
    for section in spec.sections:
        if keep is not None and not keep(section):
            continue
        handler = get_extract_handler(section.kind)
        if handler is None:
            raise HandlerError(
                f"{section.source}: no extract handler for <{section.kind.value}> "
                "(landing in a later phase)"
            )
        planned.append(section)
        for p in _planned_targets(section, files_root):
            if p.exists():
                existing.append(p)

    if existing and confirm_existing is not None:
        if not confirm_existing(existing):
            raise HandlerError(
                f"extract aborted: {len(existing)} existing file(s) would be overwritten"
            )

    results: list[ExtractedSection] = []
    for section in planned:
        handler = get_extract_handler(section.kind)
        results.append(handler(body, section, files_root))

    return ExtractResult(sections=results, duration_ms=int((perf_counter() - t0) * 1000))

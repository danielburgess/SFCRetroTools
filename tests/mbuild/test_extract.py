"""Phase 2b extract pipeline tests + round-trip extract→build verifier."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from retrotool.mbuild import (
    BuildSpec,
    HandlerError,
    Section,
    SectionKind,
    build,
    extract,
)


# Reuse fixture builder from build tests by re-implementing inline (keeps the
# files independent — tests/mbuild has no conftest yet).

_ROM_SIZE = 0x80_000


def _make_lorom(tmp_path: Path) -> Path:
    body = bytearray([0x00] * _ROM_SIZE)
    body[0x7FC0:0x7FC0 + 21] = b"TEST ROM             "
    body[0x7FD5] = 0x20
    body[0x7FD7] = 0x09
    # Plant recognizable bytes BEFORE checksum (so the on-disk csum is valid).
    body[0x100:0x108] = b"\xDE\xAD\xBE\xEF\xCA\xFE\xBA\xBE"
    body[0x7FDC:0x7FE0] = b"\xFF\xFF\x00\x00"
    csum = sum(body) & 0xFFFF
    body[0x7FDC] = (csum ^ 0xFFFF) & 0xFF
    body[0x7FDD] = ((csum ^ 0xFFFF) >> 8) & 0xFF
    body[0x7FDE] = csum & 0xFF
    body[0x7FDF] = (csum >> 8) & 0xFF
    p = tmp_path / "base.sfc"
    p.write_bytes(body)
    return p


def test_extract_rep_with_explicit_size(tmp_path):
    rom_path = _make_lorom(tmp_path)
    spec = BuildSpec(
        sections=[Section(kind=SectionKind.REP, offset=0x100, size=8,
                          files=[PurePosixPath("dump.bin")])],
    )
    result = extract(spec, source_root=tmp_path, original_rom=rom_path)
    out = tmp_path / "dump.bin"
    assert out.read_bytes() == b"\xDE\xAD\xBE\xEF\xCA\xFE\xBA\xBE"
    assert result.sections[0].bytes_read == 8


def test_extract_size_inferred_from_existing_file(tmp_path):
    rom_path = _make_lorom(tmp_path)
    target = tmp_path / "dump.bin"
    target.write_bytes(b"\x00" * 4)  # placeholder; tells extractor "read 4 bytes"
    spec = BuildSpec(
        sections=[Section(kind=SectionKind.BIN, offset=0x100,
                          files=[PurePosixPath("dump.bin")])],
    )
    extract(spec, source_root=tmp_path, original_rom=rom_path)
    assert target.read_bytes() == b"\xDE\xAD\xBE\xEF"


def test_extract_unsized_no_existing_file_errors(tmp_path):
    rom_path = _make_lorom(tmp_path)
    spec = BuildSpec(
        sections=[Section(kind=SectionKind.REP, offset=0x100,
                          files=[PurePosixPath("missing.bin")])],
    )
    with pytest.raises(HandlerError, match="cannot extract"):
        extract(spec, source_root=tmp_path, original_rom=rom_path)


def test_extract_ins_multifile_uses_existing_sizes(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "a.bin").write_bytes(b"\x00" * 4)
    (tmp_path / "b.bin").write_bytes(b"\x00" * 4)
    spec = BuildSpec(
        sections=[Section(kind=SectionKind.INS, offset=0x100,
                          files=[PurePosixPath("a.bin"), PurePosixPath("b.bin")])],
    )
    extract(spec, source_root=tmp_path, original_rom=rom_path)
    assert (tmp_path / "a.bin").read_bytes() == b"\xDE\xAD\xBE\xEF"
    assert (tmp_path / "b.bin").read_bytes() == b"\xCA\xFE\xBA\xBE"


def test_extract_read_past_eof_errors(tmp_path):
    rom_path = _make_lorom(tmp_path)
    spec = BuildSpec(
        sections=[Section(kind=SectionKind.REP, offset=_ROM_SIZE - 4, size=16,
                          files=[PurePosixPath("dump.bin")])],
    )
    with pytest.raises(HandlerError, match="exceeds ROM size"):
        extract(spec, source_root=tmp_path, original_rom=rom_path)


# ---- round-trip -----------------------------------------------------------


def test_round_trip_extract_then_build_is_identical(tmp_path):
    """Extract, then rebuild → output ROM matches original byte-for-byte."""
    rom_path = _make_lorom(tmp_path)
    original = rom_path.read_bytes()

    spec = BuildSpec(
        sections=[
            Section(kind=SectionKind.REP, offset=0x100, size=8,
                    files=[PurePosixPath("region.bin")]),
        ],
    )
    extract(spec, source_root=tmp_path, original_rom=rom_path)
    out = tmp_path / "rebuilt.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    assert out.read_bytes() == original


# ---- script extract -------------------------------------------------------


def test_script_round_trip(tmp_path):
    """Build script bytes into a ROM, then extract them back to text."""
    rom_path = _make_lorom(tmp_path)
    tbl_text = "\n".join(f"{ord(c):02X}={c}" for c in "HELLO WORLD") + "\n"
    (tmp_path / "ascii.tbl").write_text(tbl_text, encoding="utf-8")
    (tmp_path / "lines.txt").write_text("HELLO\nWORLD\n", encoding="utf-8")

    build_spec = BuildSpec(
        sections=[Section(
            kind=SectionKind.SCRIPT, offset=0x700,
            files=[PurePosixPath("lines.txt")],
            table=PurePosixPath("ascii.tbl"),
        )],
    )
    out = tmp_path / "out.sfc"
    build(build_spec, source_root=tmp_path, out_path=out, original_rom=rom_path)

    # Wipe text, then extract.
    (tmp_path / "lines.txt").unlink()
    extract_spec = BuildSpec(
        sections=[Section(
            kind=SectionKind.SCRIPT, offset=0x700, size=12,
            files=[PurePosixPath("lines.txt")],
            table=PurePosixPath("ascii.tbl"),
        )],
    )
    extract(extract_spec, source_root=tmp_path, original_rom=out)
    decoded = (tmp_path / "lines.txt").read_text(encoding="utf-8").splitlines()
    assert decoded[:2] == ["HELLO", "WORLD"]

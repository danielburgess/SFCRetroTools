"""Phase 2b extract pipeline tests + round-trip extract→build verifier."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from retrotool.build import (
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


def test_script_pointer_table_round_trip(tmp_path):
    """Ptr-driven extract → build round-trip. Covers 5-byte ctrl (FF 9C=5),
    sentinel ptr ($003C, out-of-LoROM), and normal entries. Output ROM
    must match input byte-for-byte in the ptr table + data region."""
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "t.tbl").write_text(
        "@ctrl_prefix FF\n@ctrl 9C=5\n0E=X\nBA=Y\n00=[end]\n41=A\n42=B\n",
        encoding="utf-8",
    )
    # Seed ROM: ptr table at 0x600 (3 × 2b), entries at 0x700, 0x710.
    rom = bytearray(rom_path.read_bytes())
    rom[0x600:0x602] = (0x8700).to_bytes(2, "little")   # → PC 0x700
    rom[0x602:0x604] = (0x003C).to_bytes(2, "little")   # sentinel
    rom[0x604:0x606] = (0x8710).to_bytes(2, "little")   # → PC 0x710
    rom[0x700:0x709] = bytes([0x41, 0xFF, 0x9C, 0x52, 0x0E, 0x00, 0xBA, 0x42, 0x00])
    rom[0x710:0x712] = bytes([0x41, 0x00])
    rom_path.write_bytes(bytes(rom))

    spec = BuildSpec(sections=[Section(
        kind=SectionKind.SCRIPT,
        files=[PurePosixPath("s.txt")],
        table=PurePosixPath("t.tbl"),
        pointer_table=0x600,
        pointer_size=2,
        count=3,
    )])
    # Extract → writes s.txt.
    extract(spec, source_root=tmp_path, original_rom=rom_path)
    raw = (tmp_path / "s.txt").read_bytes()
    assert raw[:2] == b"\xff\xfe"  # UTF-16 LE BOM (matches encoder + .tbl)
    text = (tmp_path / "s.txt").read_text(encoding="utf-16")
    assert "[FF][9C][52][0E][00]" in text  # ctrl span carried intact
    assert "<<$1536:1>>" in text            # sentinel entry (no [$PC])

    # Rebuild → out ROM should match input byte-for-byte in the region.
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    src = rom_path.read_bytes()
    dst = out.read_bytes()
    assert src[0x600:0x606] == dst[0x600:0x606]  # ptr table
    assert src[0x700:0x712] == dst[0x700:0x712]  # entry data


def test_script_pointer_table_ffc0_terminated_no_zero(tmp_path):
    """Regression: entry ending in a 5-byte FFC0 ctrl with NO trailing 0x00.

    Before the fix, `find_entry_end` scanned past the FFC0 params looking
    for a 0x00, walking into entry 1's bytes and swallowing it into entry
    0's body. Fix: primary entry bound is the next ptr's PC; the 0x00
    scan is only a secondary early-stop.
    """
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "t.tbl").write_text(
        "@ctrl_prefix FF\n@ctrl C0=5\n00=[end]\n41=A\n",
        encoding="utf-8",
    )
    rom = bytearray(rom_path.read_bytes())
    # Ptr table at 0x600 (2 × 2b). Entry 0 at 0x700 (5 bytes, FFC0 + 3
    # params, NO trailing 0x00). Entry 1 at 0x705 (`41 00`).
    rom[0x600:0x602] = (0x8700).to_bytes(2, "little")  # → PC 0x700
    rom[0x602:0x604] = (0x8705).to_bytes(2, "little")  # → PC 0x705
    rom[0x700:0x705] = bytes([0xFF, 0xC0, 0x00, 0xB2, 0x22])
    rom[0x705:0x707] = bytes([0x41, 0x00])
    rom_path.write_bytes(bytes(rom))

    spec = BuildSpec(sections=[Section(
        kind=SectionKind.SCRIPT,
        files=[PurePosixPath("s.txt")],
        table=PurePosixPath("t.tbl"),
        pointer_table=0x600,
        pointer_size=2,
        count=2,
    )])
    extract(spec, source_root=tmp_path, original_rom=rom_path)
    text = (tmp_path / "s.txt").read_text(encoding="utf-16")
    # Entry 0 body must NOT contain entry 1's "A" byte — confirms the
    # boundary stopped at 0x705 instead of scanning past to entry 1's 0x00.
    lines = text.splitlines()
    # Find entry 0 body (line after "<<$1536:0[...]>>").
    hdr0 = next(i for i, ln in enumerate(lines) if ln.startswith("<<$1536:0["))
    entry0_body = lines[hdr0 + 1]
    assert "[FF][C0][00][B2][22]" in entry0_body
    assert "A" not in entry0_body  # entry 1's byte must not leak in

    # Round-trip: rebuilt ROM matches src across ptr table + data region.
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    src = rom_path.read_bytes()
    dst = out.read_bytes()
    assert src[0x600:0x604] == dst[0x600:0x604]
    assert src[0x700:0x707] == dst[0x700:0x707]


def test_script_pointer_table_duplicate_src_ptr_dedupe(tmp_path):
    """Regression: duplicate source pointers share one output placement.

    Ptr table [A, B, A, C] — slots 0 and 2 point at the same source PC.
    Without dedupe, slot 2's entry re-writes at `cur`, drifting every
    later ptr. Fix: when an earlier entry already placed at the same
    source PC, reuse its output PC and skip the data write.
    """
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "t.tbl").write_text(
        "@ctrl_prefix FF\n00=[end]\n41=A\n42=B\n43=C\n",
        encoding="utf-8",
    )
    rom = bytearray(rom_path.read_bytes())
    # Ptr table at 0x600 (4 × 2b).
    # A=0x700 (AA 00), B=0x705 (BB 00), C=0x70A (CC 00). Slots 0 & 2 → A.
    rom[0x600:0x602] = (0x8700).to_bytes(2, "little")
    rom[0x602:0x604] = (0x8705).to_bytes(2, "little")
    rom[0x604:0x606] = (0x8700).to_bytes(2, "little")  # dup of slot 0
    rom[0x606:0x608] = (0x870A).to_bytes(2, "little")
    rom[0x700:0x702] = bytes([0x41, 0x00])
    rom[0x705:0x707] = bytes([0x42, 0x00])
    rom[0x70A:0x70C] = bytes([0x43, 0x00])
    rom_path.write_bytes(bytes(rom))

    spec = BuildSpec(sections=[Section(
        kind=SectionKind.SCRIPT,
        files=[PurePosixPath("s.txt")],
        table=PurePosixPath("t.tbl"),
        pointer_table=0x600,
        pointer_size=2,
        count=4,
    )])
    extract(spec, source_root=tmp_path, original_rom=rom_path)
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)

    src = rom_path.read_bytes()
    dst = out.read_bytes()
    # Ptr table byte-equal — slot 2 must equal slot 0, no drift.
    assert dst[0x600:0x608] == src[0x600:0x608]
    # Data region byte-equal.
    assert dst[0x700:0x70C] == src[0x700:0x70C]


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
    decoded = (tmp_path / "lines.txt").read_text(encoding="utf-16").splitlines()
    assert decoded[:2] == ["HELLO", "WORLD"]

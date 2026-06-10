"""pointers.offset / pointer-table= is a PC FILE OFFSET — everywhere.

Regression tests for the extract-vs-build disagreement found 2026-06-10:
extract used a SNES-first interpretation of `section.pointer_table` while
the build handlers always read it as PC. The two coincide numerically in
HiROM's mirror ranges (so HiROM projects never noticed), but in LoROM a
pointer table at PC 0x8000-0xFFFF was unconfigurable: PC-authored values
extracted from the wrong place (SNES $00:8000 -> PC 0), SNES-authored
values crashed the build with `NoneType >> int`.

Both sides now resolve through `handlers._resolve_pointer_table_pc`, which
also bounds-checks the read and diagnoses SNES-authored values by naming
the PC offset to use instead.
"""
from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from retrotool.build import BuildSpec, Section, SectionKind, build
from retrotool.build.extract import extract
from retrotool.build.handlers import HandlerError
from tests.build.conftest import _make_lorom

_TBL = "\n".join(f"{ord(c):02X}={c}" for c in "ABCDE") + "\n00=[end]\n"


def _spec(ptr_table: int, count: int = 2) -> BuildSpec:
    return BuildSpec(sections=[Section(
        kind=SectionKind.SCRIPT,
        files=[PurePosixPath("s.txt")],
        table=PurePosixPath("t.tbl"),
        pointer_table=ptr_table,
        pointer_size=2,
        count=count,
        placement={"mode": "relocate"},
    )])


def test_lorom_table_at_pc_8000_extracts_and_builds(tmp_path):
    """The formerly-impossible case: a LoROM pointer table in PC
    0x8000-0xFFFF (= bank $81). PC-authored, both pipelines agree."""
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "t.tbl").write_text(_TBL, encoding="utf-8")
    rom = bytearray(rom_path.read_bytes())
    # Table at PC 0x8000: 16-bit within-bank pointers, bank $81 implied.
    # Strings at PC 0x8040 ("AB\0") and 0x8043 ("C\0").
    rom[0x8000:0x8002] = (0x8040).to_bytes(2, "little")
    rom[0x8002:0x8004] = (0x8043).to_bytes(2, "little")
    rom[0x8040:0x8043] = b"AB\x00"
    rom[0x8043:0x8045] = b"C\x00"
    rom_path.write_bytes(bytes(rom))

    spec = _spec(0x8000)
    extract(spec, source_root=tmp_path, original_rom=rom_path)
    text = (tmp_path / "s.txt").read_text(encoding="utf-16")
    # Headers carry the real PCs (32768 = 0x8000, 32832 = 0x8040)…
    assert "<<$32768:0[$32832]>>" in text
    # …and the decoded strings, not garbage read from PC 0.
    assert "AB" in text and "C" in text

    build(spec, source_root=tmp_path,
          out_path=tmp_path / "out.sfc", original_rom=rom_path)
    out = (tmp_path / "out.sfc").read_bytes()
    ptr0 = int.from_bytes(out[0x8000:0x8002], "little")
    pc0 = (0x8000 & ~0x7FFF) | (ptr0 & 0x7FFF)
    assert out[pc0:pc0 + 2] == b"AB"


def test_snes_authored_value_diagnosed_on_extract_and_build(tmp_path):
    """A SNES-authored offset ($818000 for the table at PC 0x8000) now
    fails LOUDLY on both pipelines, naming the PC offset to author."""
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "t.tbl").write_text(_TBL, encoding="utf-8")
    (tmp_path / "s.txt").write_text(
        "<<$32768:0[$32832]>>\nAB\n", encoding="utf-8")

    spec = _spec(0x818000)
    with pytest.raises(HandlerError, match=r"\$008000 instead"):
        extract(spec, source_root=tmp_path, original_rom=rom_path)
    with pytest.raises(HandlerError, match=r"\$008000 instead"):
        build(spec, source_root=tmp_path,
              out_path=tmp_path / "out.sfc", original_rom=rom_path)


def test_plain_out_of_bounds_offset_errors_clearly(tmp_path):
    """An offset that is garbage under BOTH interpretations reports a
    bounds error (not the SNES suggestion, and never NoneType >> int)."""
    rom_path = _make_lorom(tmp_path)   # 512 KB
    (tmp_path / "t.tbl").write_text(_TBL, encoding="utf-8")
    (tmp_path / "s.txt").write_text(
        "<<$32768:0[$32832]>>\nAB\n", encoding="utf-8")

    spec = _spec(0x7F0000)   # 8 MB: invalid PC and unmapped SNES (WRAM bank)
    with pytest.raises(HandlerError, match="exceeds ROM size"):
        build(spec, source_root=tmp_path,
              out_path=tmp_path / "out.sfc", original_rom=rom_path)
    with pytest.raises(HandlerError, match="exceeds ROM size"):
        extract(spec, source_root=tmp_path, original_rom=rom_path)


def test_hirom_coinciding_value_still_works(tmp_path):
    """rbshura-style authoring: in HiROM the documented convention writes
    the PC offset (which numerically equals the SNES form in the mirror
    range). Behavior is unchanged by the fix."""
    rom_path = _make_lorom(tmp_path)
    # Reuse the LoROM image but exercise the low-PC path shared with HiROM
    # coincidence values: a table below 0x8000 resolved as plain PC.
    (tmp_path / "t.tbl").write_text(_TBL, encoding="utf-8")
    rom = bytearray(rom_path.read_bytes())
    rom[0x600:0x602] = (0x8700).to_bytes(2, "little")    # → PC 0x700
    rom[0x700:0x703] = b"DE\x00"
    rom_path.write_bytes(bytes(rom))

    spec = _spec(0x600, count=1)
    extract(spec, source_root=tmp_path, original_rom=rom_path)
    text = (tmp_path / "s.txt").read_text(encoding="utf-16")
    assert "<<$1536:0[$1792]>>" in text and "DE" in text

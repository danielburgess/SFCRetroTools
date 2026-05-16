"""Regression tests for retrotool.script.extractor.

The critical case: a control code whose parameter byte equals the entry
terminator must NOT prematurely cut the extracted entry. The byte-walk in
the old `_read_until` did exactly that; the fix routes through
`Table.find_entry_end()` whenever the table declares `@ctrl` sequences.
"""
from __future__ import annotations

from pathlib import Path

from retrotool.core.address import SFCAddressType
from retrotool.project.datadef import (
    DataDef,
    DataSection,
    EncodingSection,
    PointersSection,
)
from retrotool.script.extractor import extract_script
from retrotool.script.table import Table


def _write_table(tmp_path: Path, body: str, name: str = "t.tbl") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _build_datadef(name: str, ptr_off: int, count: int,
                   data_off: int, data_end: int,
                   tbl_path: Path, terminator: int) -> DataDef:
    return DataDef(
        name=name,
        type="pointer",
        encoding=EncodingSection(table_file=tbl_path, terminator=terminator),
        pointers=PointersSection(offset=ptr_off, count=count, size=2),
        data=DataSection(offset=data_off, end=data_end),
    )


def test_extract_ctrl_with_zero_param_byte_does_not_truncate(tmp_path):
    """@ctrl CC=4 → 'FF C0 xx yy' is a 4-byte control code.
    String contains FF C0 00 00 (param bytes happen to be 0x00).
    Old `_read_until` stops at the first 0x00 inside the ctrl, returning
    a 5-byte truncated entry. The fix walks the ctrl_length and lands on
    the real 0x00 terminator at the end of the entry.
    """
    tbl_path = _write_table(tmp_path, (
        "41=A\n"
        "42=B\n"
        "43=C\n"
        "44=D\n"
        "@ctrl_prefix FF\n"
        "@ctrl C0=4\n"     # FF C0 xx yy → 4 bytes total
        "00=[end]\n"
    ))
    table = Table(tbl_path)
    assert table.ctrl_lengths, "test table must declare @ctrl entries"

    # Lay out a tiny ROM:
    #   pointer table @ 0x0000:  [04 00] [10 00]    (entry 0 → 0x04, entry 1 → 0x10)
    #   data block    @ 0x0004:  41 42 FF C0 00 00 00     (ABC + 4-byte ctrl + terminator)
    #   data block    @ 0x0010:  43 44 00                  (CD + terminator)
    rom = bytearray(0x100)
    # Pointers (2-byte LE)
    rom[0x00] = 0x04; rom[0x01] = 0x00      # → 0x0004
    rom[0x02] = 0x10; rom[0x03] = 0x00      # → 0x0010
    # Entry 0
    rom[0x04] = 0x41                         # A
    rom[0x05] = 0x42                         # B
    rom[0x06] = 0xFF                         # ctrl prefix
    rom[0x07] = 0xC0                         # ctrl cmd
    rom[0x08] = 0x00                         # param byte (== terminator!)
    rom[0x09] = 0x00                         # param byte (== terminator!)
    rom[0x0A] = 0x00                         # real terminator
    # Entry 1
    rom[0x10] = 0x43                         # C
    rom[0x11] = 0x44                         # D
    rom[0x12] = 0x00                         # terminator

    datadef = _build_datadef(
        name="dlg",
        ptr_off=0x0000, count=2,
        data_off=0x0004, data_end=0x0020,
        tbl_path=tbl_path, terminator=0x00,
    )

    script = extract_script(bytes(rom), datadef, table,
                            address_type=SFCAddressType.PC)

    assert len(script.entries) == 2
    # Entry 0 must capture the full 7-byte sequence including the embedded
    # FF C0 00 00 (control) + 00 (terminator). The old code would have cut
    # at byte 4 (the first 0x00) and returned a 5-byte raw.
    assert script.entries[0].raw == b"\x41\x42\xFF\xC0\x00\x00\x00"
    # Entry 1 is unaffected.
    assert script.entries[1].raw == b"\x43\x44\x00"


def test_extract_no_ctrl_uses_simple_byte_walk(tmp_path):
    """Table without @ctrl entries → extractor falls back to _read_until.
    Confirms the existing path still works (no regression)."""
    tbl_path = _write_table(tmp_path, "41=A\n42=B\n00=[end]\n")
    table = Table(tbl_path)
    assert not table.ctrl_lengths

    rom = bytearray(0x100)
    rom[0x00] = 0x04; rom[0x01] = 0x00
    rom[0x04] = 0x41; rom[0x05] = 0x42; rom[0x06] = 0x00

    datadef = _build_datadef(
        name="plain",
        ptr_off=0x0000, count=1,
        data_off=0x0004, data_end=0x0010,
        tbl_path=tbl_path, terminator=0x00,
    )

    script = extract_script(bytes(rom), datadef, table,
                            address_type=SFCAddressType.PC)
    assert len(script.entries) == 1
    assert script.entries[0].raw == b"\x41\x42\x00"


def test_extract_ctrl_with_nonzero_terminator(tmp_path):
    """`terminator` may differ from 0x00. Confirms find_entry_end honors
    the requested terminator and still walks ctrl_lengths past parameter
    bytes (here the param byte is 0xAA which also happens to be the
    terminator — same shape of bug as the 0x00 case)."""
    tbl_path = _write_table(tmp_path, (
        "41=A\n"
        "42=B\n"
        "@ctrl_prefix FF\n"
        "@ctrl C0=3\n"      # FF C0 xx → 3 bytes total
        "AA=[end]\n"
    ))
    table = Table(tbl_path)
    assert table.ctrl_lengths

    rom = bytearray(0x100)
    rom[0x00] = 0x04; rom[0x01] = 0x00
    rom[0x04] = 0x41                         # A
    rom[0x05] = 0xFF                         # ctrl prefix
    rom[0x06] = 0xC0                         # ctrl cmd
    rom[0x07] = 0xAA                         # param byte (== terminator!)
    rom[0x08] = 0x42                         # B
    rom[0x09] = 0xAA                         # real terminator

    datadef = _build_datadef(
        name="dlg",
        ptr_off=0x0000, count=1,
        data_off=0x0004, data_end=0x0020,
        tbl_path=tbl_path, terminator=0xAA,
    )

    script = extract_script(bytes(rom), datadef, table,
                            address_type=SFCAddressType.PC)
    assert len(script.entries) == 1
    assert script.entries[0].raw == b"\x41\xFF\xC0\xAA\x42\xAA"

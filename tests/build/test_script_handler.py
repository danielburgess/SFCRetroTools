"""End-to-end handle_script tests: pointer-table + fixups + global labels."""
from __future__ import annotations

import textwrap
from pathlib import Path, PurePosixPath

from retrotool.build import (
    BuildSpec, Section, SectionKind, build, parse_project_toml,
)
from retrotool.build.handlers import handle_script, BuildContext, WriteRange
from tests.build.conftest import _make_lorom


_ASCII_TBL = "\n".join(f"{ord(c):02X}={c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ abcd") + "\n"


def test_script_pointer_table_emits_table_and_data(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "t.tbl").write_text(_ASCII_TBL, encoding="utf-8")
    (tmp_path / "s.txt").write_text(
        "<<$C000:0[$100]>>\nABC\n<<$C000:1[$110]>>\nDE\n",
        encoding="utf-8",
    )
    spec = BuildSpec(
        sections=[Section(
            kind=SectionKind.SCRIPT,
            files=[PurePosixPath("s.txt")],
            table=PurePosixPath("t.tbl"),
            pointer_table=0x600,
            pointer_size=2,
            count=2,
            placement={"mode": "relocate"},
        )],
    )
    result = build(spec, source_root=tmp_path,
                   out_path=tmp_path / "out.sfc", original_rom=rom_path)
    # multi-range writes: pointer table + per-entry inline
    writes = result.sections[0].write
    assert writes[0].offset == 0x600 and writes[0].length == 4
    body = (tmp_path / "out.sfc").read_bytes()
    data_start = 0x600 + 4
    # entry 0 "ABC" at data_start; entry 1 "DE" right after
    assert body[data_start:data_start + 3] == b"ABC"
    assert body[data_start + 3:data_start + 5] == b"DE"
    # pointer table: 16-bit within-bank LoROM addresses (addr | 0x8000)
    ptr0 = int.from_bytes(body[0x600:0x602], "little")
    ptr1 = int.from_bytes(body[0x602:0x604], "little")
    assert ptr0 == ((data_start) & 0x7FFF) | 0x8000
    assert ptr1 == ((data_start + 3) & 0x7FFF) | 0x8000


def test_script_global_label_ref_resolves(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "t.tbl").write_text(_ASCII_TBL, encoding="utf-8")
    (tmp_path / "s.txt").write_text(
        "<<$C000:0[$100]>>\nA[FFC0@@dte_start]\n",
        encoding="utf-8",
    )
    spec = BuildSpec(
        labels={"dte_start": 0x230000},
        sections=[Section(
            kind=SectionKind.SCRIPT,
            files=[PurePosixPath("s.txt")],
            table=PurePosixPath("t.tbl"),
            pointer_table=0x600,
            pointer_size=2,
            count=1,
            placement={"mode": "relocate"},
        )],
    )
    build(spec, source_root=tmp_path,
          out_path=tmp_path / "out.sfc", original_rom=rom_path)
    body = (tmp_path / "out.sfc").read_bytes()
    data_start = 0x602
    # entry = A FF C0 <3-byte-addr-of-0x230000-LoROM1>
    assert body[data_start:data_start + 3] == b"A\xFF\xC0"
    # 0x230000 → LoROM1: bank=((0x230000>>15)&0x7F)|0x80 = 0x80|0x46=0xC6,
    # addr=(0x230000&0x7FFF)|0x8000 = 0x8000
    assert body[data_start + 3:data_start + 6] == bytes([0x00, 0x80, 0xC6])


def test_script_entry_ref_with_label(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "t.tbl").write_text(_ASCII_TBL, encoding="utf-8")
    (tmp_path / "s.txt").write_text(
        "<<$C000:0[$100]>>\n[FFC0@1:tag]\n"
        "<<$C000:1[$110]>>\nAA[label:tag]BB\n",
        encoding="utf-8",
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.SCRIPT,
        files=[PurePosixPath("s.txt")],
        table=PurePosixPath("t.tbl"),
        pointer_table=0x600,
        pointer_size=2,
        count=2,
        placement={"mode": "relocate"},
    )])
    build(spec, source_root=tmp_path,
          out_path=tmp_path / "out.sfc", original_rom=rom_path)
    body = (tmp_path / "out.sfc").read_bytes()
    # entry 0 = FF C0 <addr of entry1 + 2 (past "AA")>
    entry0 = 0x604  # ptr table = 4 bytes, entry 0 starts here
    # entry 0 length = 5 bytes (FF C0 + 3-byte ptr)
    entry1 = entry0 + 5
    target_pc = entry1 + 2
    expected_bank = ((target_pc >> 15) & 0x7F) | 0x80
    expected_addr = (target_pc & 0x7FFF) | 0x8000
    assert body[entry0:entry0 + 2] == b"\xFF\xC0"
    assert body[entry0 + 2] == expected_addr & 0xFF
    assert body[entry0 + 3] == (expected_addr >> 8) & 0xFF
    assert body[entry0 + 4] == expected_bank


def test_script_sentinel_ptr_passthrough(tmp_path):
    """Entries whose source ptr decodes outside the LoROM window are
    carried through unchanged and consume no data-region space."""
    rom_path = _make_lorom(tmp_path)
    # Pre-seed the ptr table at 0x600 so the handler sees existing source ptrs:
    #   idx 0 → $8000 (valid, PC 0), idx 1 → $003C (invalid — system mirror),
    #   idx 2 → $8003 (valid, PC 3).
    # Bank bits come from the table's own PC (0x600 → LoROM1 bank 0x80).
    rom = bytearray(rom_path.read_bytes())
    rom[0x600:0x606] = bytes([0x00, 0x80, 0x3C, 0x00, 0x03, 0x80])
    rom_path.write_bytes(bytes(rom))

    (tmp_path / "t.tbl").write_text(_ASCII_TBL, encoding="utf-8")
    (tmp_path / "s.txt").write_text(
        "<<$C000:0[$1]>>\nA\n"
        "<<$C000:1[$2]>>\nB\n"        # sentinel — ptr decodes to None
        "<<$C000:2[$3]>>\nC\n",
        encoding="utf-8",
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.SCRIPT,
        files=[PurePosixPath("s.txt")],
        table=PurePosixPath("t.tbl"),
        pointer_table=0x600,
        pointer_size=2,
        count=3,
        placement={"mode": "relocate"},
    )])
    build(spec, source_root=tmp_path,
          out_path=tmp_path / "out.sfc", original_rom=rom_path)
    body = (tmp_path / "out.sfc").read_bytes()
    # Sentinel ptr preserved verbatim.
    assert body[0x602:0x604] == b"\x3C\x00"
    # Entry 0 "A" at PC 0, entry 2 "C" at PC 3 — untouched source slots.
    assert body[0x000] == ord("A")
    assert body[0x003] == ord("C")
    # No "B" byte written anywhere in the data region (sentinel skipped).
    assert b"B" not in body[:0x600]


def test_script_from_toml_and_labels(tmp_path):
    """Full loop: project.toml with [[build.labels]] → build → parse OK, fixups resolve."""
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "t.tbl").write_text(_ASCII_TBL, encoding="utf-8")
    (tmp_path / "s.txt").write_text(
        "<<$C000:0[$100]>>\nA[FFC0@@audio]\n", encoding="utf-8",
    )
    (tmp_path / "project.toml").write_text(textwrap.dedent(f"""
        [rom]
        file = "{rom_path.name}"

        [[rom.build.labels]]
        name = "audio"
        at = 0x230000

        [rom.build.section.placement]
        mode = "relocate"

        [[rom.build.sections]]
        kind = "script"
        file = "s.txt"
        table = "t.tbl"
        pointer-table = 0x600
        pointer-size = 2
        count = 1
    """), encoding="utf-8")
    spec = parse_project_toml(tmp_path / "project.toml")
    assert spec.labels["audio"] == 0x230000
    build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc")
    body = (tmp_path / "out.sfc").read_bytes()
    assert body[0x602:0x605] == b"A\xFF\xC0"

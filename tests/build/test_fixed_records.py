"""Phase 6b <fixed-records>: stride×count validator + raw-binary build/extract."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from retrotool.build import (
    BuildSpec, HandlerError, Section, SectionKind, build, extract,
    parse_mbxml_string, parse_project_toml_dict,
)


_ROM_SIZE = 0x80_000


def _make_lorom(tmp_path: Path) -> Path:
    body = bytearray(_ROM_SIZE)
    body[0x7FC0:0x7FC0 + 21] = b"TEST ROM             "
    body[0x7FD5] = 0x20
    body[0x7FD7] = 0x09
    csum = sum(body) & 0xFFFF
    comp = csum ^ 0xFFFF
    body[0x7FDC:0x7FE0] = bytes([comp & 0xFF, (comp >> 8) & 0xFF,
                                 csum & 0xFF, (csum >> 8) & 0xFF])
    p = tmp_path / "base.sfc"
    p.write_bytes(body)
    return p


def test_fixed_records_writes_packed_binary(tmp_path):
    rom_path = _make_lorom(tmp_path)
    # 4 records × 8 bytes
    (tmp_path / "items.bin").write_bytes(bytes(range(32)))
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x1000,
        files=[PurePosixPath("items.bin")], stride=8, count=4,
    )])
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    assert out.read_bytes()[0x1000:0x1020] == bytes(range(32))


def test_fixed_records_size_mismatch_raises(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "items.bin").write_bytes(b"\x00" * 30)  # not 8*4
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x1000,
        files=[PurePosixPath("items.bin")], stride=8, count=4,
    )])
    with pytest.raises(HandlerError, match="expected stride"):
        build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path)


def test_fixed_records_stride_only_validates_alignment(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "items.bin").write_bytes(b"\x00" * 30)
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x1000,
        files=[PurePosixPath("items.bin")], stride=8,
    )])
    with pytest.raises(HandlerError, match="multiple of stride"):
        build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path)


def test_fixed_records_extract_round_trips(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "items.bin").write_bytes(bytes(range(32)))
    spec = BuildSpec(
        original=PurePosixPath("base.sfc"),
        sections=[Section(
            kind=SectionKind.FIXED_RECORDS, offset=0x1000,
            files=[PurePosixPath("items.bin")], stride=8, count=4,
        )],
    )
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)

    # Wipe the source file, extract from rebuilt ROM, verify match.
    (tmp_path / "items.bin").unlink()
    extract(spec, source_root=tmp_path, original_rom=out, dest_root=tmp_path)
    assert (tmp_path / "items.bin").read_bytes() == bytes(range(32))


def _make_abc_table(tmp_path: Path) -> Path:
    p = tmp_path / "abc.tbl"
    p.write_text("41=A\n42=B\n43=C\n44=D\n45=E\n46=F\n47=G\n48=H\n49=I\n4A=J\n"
                 "4B=K\n4C=L\n4D=M\n4E=N\n4F=O\n50=P\n51=Q\n52=R\n53=S\n54=T\n"
                 "55=U\n56=V\n57=W\n20= \n", encoding="utf-8")
    return p


def test_fixed_records_packs_from_text_script(tmp_path):
    """Text-mode: `<<$HEX:idx.label>>` script → padded records with
    non-field bytes preserved from ROM."""
    rom_path = _make_lorom(tmp_path)
    _make_abc_table(tmp_path)
    script = (
        "<<$12345:0.name>>\n"
        "ABC\n"
        "<<$12345:1.name>>\n"
        "HELLO\n"
    )
    (tmp_path / "t.txt").write_text(script, encoding="utf-8")
    # 2 records × stride 8; field `name` at offset 0 len 5 fill 0x20.
    # Bytes 5-7 per record stay untouched (base ROM is zeros).
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x1000,
        files=[PurePosixPath("t.txt")],
        stride=8, count=2,
        table=PurePosixPath("abc.tbl"),
        fields=[{"label": "name", "start": 0, "len": 5, "fill": 0x20}],
    )])
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    # Rec0: "ABC" + 2 spaces + 3 zero bytes (non-field, preserved from base).
    assert out.read_bytes()[0x1000:0x1005] == b"ABC\x20\x20"
    assert out.read_bytes()[0x1005:0x1008] == b"\x00\x00\x00"
    # Rec1: "HELLO" (exactly fills field) + 3 zero bytes.
    assert out.read_bytes()[0x1008:0x100D] == b"HELLO"
    assert out.read_bytes()[0x100D:0x1010] == b"\x00\x00\x00"


def test_fixed_records_text_rejects_oversize_entry(tmp_path):
    """Oversize fields raise HandlerError. Previously silently truncated,
    which masked encoder/budget bugs by producing healthy-looking ROMs whose
    renderers walked past missing terminators into adjacent data."""
    rom_path = _make_lorom(tmp_path)
    _make_abc_table(tmp_path)
    (tmp_path / "t.txt").write_text(
        "<<$12345:0.name>>\nABCDEFGHIJ\n", encoding="utf-8"
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x1000,
        files=[PurePosixPath("t.txt")],
        stride=5, count=1,
        table=PurePosixPath("abc.tbl"),
        fields=[{"label": "name", "start": 0, "len": 5, "fill": 0x20}],
    )])
    with pytest.raises(HandlerError, match=r"encodes to 10 B but budget is 5 B"):
        build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path)


def test_fixed_records_text_rejects_unknown_label(tmp_path):
    rom_path = _make_lorom(tmp_path)
    _make_abc_table(tmp_path)
    (tmp_path / "t.txt").write_text(
        "<<$12345:0.nope>>\nABC\n", encoding="utf-8"
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x1000,
        files=[PurePosixPath("t.txt")],
        stride=8, count=1,
        table=PurePosixPath("abc.tbl"),
        fields=[{"label": "name", "start": 0, "len": 5, "fill": 0x20}],
    )])
    with pytest.raises(HandlerError, match="unknown field label"):
        build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path)


def test_fixed_records_text_rejects_out_of_range_idx(tmp_path):
    rom_path = _make_lorom(tmp_path)
    _make_abc_table(tmp_path)
    (tmp_path / "t.txt").write_text(
        "<<$12345:9.name>>\nABC\n", encoding="utf-8"
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x1000,
        files=[PurePosixPath("t.txt")],
        stride=8, count=2,
        table=PurePosixPath("abc.tbl"),
        fields=[{"label": "name", "start": 0, "len": 5, "fill": 0x20}],
    )])
    with pytest.raises(HandlerError, match="exceeds count"):
        build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path)


def test_fixed_records_text_multi_field(tmp_path):
    """Two fields per record (weapon + armor) — matches LM3 unit-equipment."""
    rom_path = _make_lorom(tmp_path)
    _make_abc_table(tmp_path)
    (tmp_path / "t.txt").write_text(
        "<<$12345:0.weapon>>\nAXE\n"
        "<<$12345:0.armor>>\nHELM\n",
        encoding="utf-8",
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x1000,
        files=[PurePosixPath("t.txt")],
        stride=0x10, count=1,
        table=PurePosixPath("abc.tbl"),
        fields=[
            {"label": "weapon", "start": 0x0, "len": 0x6, "fill": 0x20},
            {"label": "armor",  "start": 0x8, "len": 0x6, "fill": 0x20},
        ],
    )])
    build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
          original_rom=rom_path)
    rom = (tmp_path / "out.sfc").read_bytes()
    assert rom[0x1000:0x1006] == b"AXE\x20\x20\x20"
    assert rom[0x1008:0x100E] == b"HELM\x20\x20"


def test_fixed_records_field_ptr_writes(tmp_path):
    """A field with `ptr_writes` auto-emits N pointer-table entries holding
    its runtime address. Lets users keep pointer-table slots in sync with
    field layout without hardcoding offsets in a separate asar patch."""
    rom_path = _make_lorom(tmp_path)
    _make_abc_table(tmp_path)
    (tmp_path / "t.txt").write_text(
        "<<$12345:0.first>>\nA\n"
        "<<$12345:0.second>>\nB\n",
        encoding="utf-8",
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x2000,
        files=[PurePosixPath("t.txt")],
        stride=0x10, count=1,
        table=PurePosixPath("abc.tbl"),
        fields=[
            {"label": "first",  "start": 0, "len": 4, "fill": 0x00},
            # `second` field lives at offset $2000 + 4 = $2004 (within-bank
            # address $2004). Three pointers should land at $0100, $0102,
            # $0104, each containing $04 $20 (little-endian $2004).
            {"label": "second", "start": 4, "len": 4, "fill": 0x00,
             "ptr_writes": [
                 {"addr": "$100", "count": 3, "size": 2, "format": "within-bank"}
             ]},
        ],
    )])
    build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
          original_rom=rom_path)
    rom = (tmp_path / "out.sfc").read_bytes()
    assert rom[0x2000:0x2008] == b"A\x00\x00\x00B\x00\x00\x00"
    # 3 × 16-bit little-endian $2004 at $0100..$0105
    assert rom[0x0100:0x0106] == b"\x04\x20" * 3


def test_fixed_records_field_ptr_writes_file_offset_3byte(tmp_path):
    """`format='file-offset' size=3` writes a 24-bit little-endian file
    offset (useful for cross-bank pointer tables)."""
    rom_path = _make_lorom(tmp_path)
    _make_abc_table(tmp_path)
    (tmp_path / "t.txt").write_text(
        "<<$12345:0.name>>\nA\n", encoding="utf-8"
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x1F0000 if False else 0x12345,
        files=[PurePosixPath("t.txt")],
        stride=8, count=1,
        table=PurePosixPath("abc.tbl"),
        fields=[
            {"label": "name", "start": 0, "len": 4, "fill": 0x00,
             "ptr_writes": [
                 {"addr": "$200", "count": 2, "size": 3, "format": "file-offset"}
             ]},
        ],
    )])
    build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
          original_rom=rom_path)
    rom = (tmp_path / "out.sfc").read_bytes()
    # 2 × 24-bit little-endian $012345 at $0200..$0205
    assert rom[0x0200:0x0206] == b"\x45\x23\x01" * 2


def test_fixed_records_field_ptr_writes_rejects_bad_format(tmp_path):
    rom_path = _make_lorom(tmp_path)
    _make_abc_table(tmp_path)
    (tmp_path / "t.txt").write_text("<<$12345:0.x>>\nA\n", encoding="utf-8")
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x1000,
        files=[PurePosixPath("t.txt")],
        stride=4, count=1, table=PurePosixPath("abc.tbl"),
        fields=[{"label": "x", "start": 0, "len": 4, "fill": 0,
                 "ptr_writes": [{"addr": "$100", "count": 1, "size": 2,
                                 "format": "nope"}]}],
    )])
    with pytest.raises(HandlerError, match=r"unknown format 'nope'"):
        build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path)


def test_fixed_records_auto_pack_fields(tmp_path):
    """Fields that omit `start`/`len` auto-pack: first field starts at 0,
    each subsequent field starts immediately after the previous, and each
    field's length equals its encoded content length."""
    rom_path = _make_lorom(tmp_path)
    _make_abc_table(tmp_path)
    (tmp_path / "t.txt").write_text(
        "<<$12345:0.head>>\nABC\n"
        "<<$12345:0.tail>>\nWVU\n",
        encoding="utf-8",
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x3000,
        files=[PurePosixPath("t.txt")],
        count=1,                       # stride is auto-derived
        table=PurePosixPath("abc.tbl"),
        fields=[
            {"label": "head", "fill": 0x00},      # no start, no len
            {"label": "tail", "fill": 0x00},      # auto-packs after head
        ],
    )])
    build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
          original_rom=rom_path)
    rom = (tmp_path / "out.sfc").read_bytes()
    # head=ABC (3B) at offset 0, tail=WVU (3B) at offset 3 → 6 B total.
    assert rom[0x3000:0x3006] == b"ABC" + b"WVU"


def test_fixed_records_auto_pack_with_ptr_writes(tmp_path):
    """Auto-pack + ptr_writes: the pointer table receives the auto-computed
    start address, so adding content to an earlier field shifts later
    fields' pointers automatically."""
    rom_path = _make_lorom(tmp_path)
    _make_abc_table(tmp_path)
    # "ABCD" (4B) followed by tail field gets packed at offset 4.
    (tmp_path / "t.txt").write_text(
        "<<$12345:0.head>>\nABCD\n"
        "<<$12345:0.tail>>\nW\n",
        encoding="utf-8",
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x4000, count=1,
        files=[PurePosixPath("t.txt")],
        table=PurePosixPath("abc.tbl"),
        fields=[
            {"label": "head", "fill": 0x00},   # auto-size 4
            {"label": "tail", "fill": 0x00,
             "ptr_writes": [
                 # 3 × 16-bit pointers at $200 holding tail's address.
                 # tail lands at $4000 + 4 = $4004; within-bank = $4004.
                 {"addr": "$200", "count": 3, "size": 2, "format": "within-bank"},
             ]},
        ],
    )])
    build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
          original_rom=rom_path)
    rom = (tmp_path / "out.sfc").read_bytes()
    assert rom[0x4000:0x4005] == b"ABCDW"
    # 3 × LE-16 of $4004 = $04 $40
    assert rom[0x0200:0x0206] == b"\x04\x40" * 3


def test_fixed_records_auto_pack_requires_single_record(tmp_path):
    """Auto-pack across multiple records is illegal — each record's layout
    would depend on its own content, breaking fixed-stride uniformity.
    Declaring `stride` doesn't change the rule, since a field with auto
    `len` still varies per-record."""
    rom_path = _make_lorom(tmp_path)
    _make_abc_table(tmp_path)
    (tmp_path / "t.txt").write_text("<<$12345:0.x>>\nA\n", encoding="utf-8")
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x5000,
        stride=8, count=2,  # declare stride so the lower-level check fires
        files=[PurePosixPath("t.txt")],
        table=PurePosixPath("abc.tbl"),
        # Auto-len + count=2 → must raise
        fields=[{"label": "x", "fill": 0x00}],
    )])
    with pytest.raises(HandlerError, match=r"auto-pack fields.*require count=1"):
        build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path)


def test_fixed_records_text_utf16_bom(tmp_path):
    """UTF-16 LE source (common from Windows editors)."""
    rom_path = _make_lorom(tmp_path)
    _make_abc_table(tmp_path)
    (tmp_path / "t.txt").write_bytes(
        "<<$12345:0.name>>\nABC\n".encode("utf-16")
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x1000,
        files=[PurePosixPath("t.txt")],
        stride=8, count=1,
        table=PurePosixPath("abc.tbl"),
        fields=[{"label": "name", "start": 0, "len": 5, "fill": 0x20}],
    )])
    build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
          original_rom=rom_path)
    assert (tmp_path / "out.sfc").read_bytes()[0x1000:0x1005] == b"ABC\x20\x20"


def test_fixed_records_text_requires_fields(tmp_path):
    rom_path = _make_lorom(tmp_path)
    _make_abc_table(tmp_path)
    (tmp_path / "t.txt").write_text(
        "<<$12345:0.name>>\nABC\n", encoding="utf-8"
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.FIXED_RECORDS, offset=0x1000,
        files=[PurePosixPath("t.txt")],
        stride=8, count=1,
        table=PurePosixPath("abc.tbl"),
    )])
    with pytest.raises(HandlerError, match="no field schema"):
        build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path)


def test_fixed_records_mbxml_attrs_parsed():
    spec = parse_mbxml_string(
        '<build><fixed-records file="t.bin" offset="100" stride="4" count="2"/></build>'
    )
    s = spec.sections[0]
    assert s.kind is SectionKind.FIXED_RECORDS
    assert s.stride == 4 and s.count == 2 and s.offset == 0x100


def test_fixed_records_toml_attrs_parsed():
    spec = parse_project_toml_dict({
        "rom": {"build": {
            "sections": [{
                "kind": "fixed-records", "file": "t.bin",
                "offset": "0x100", "stride": 4, "count": 2,
            }]
        }}
    })
    s = spec.sections[0]
    assert s.kind is SectionKind.FIXED_RECORDS
    assert s.stride == 4 and s.count == 2 and s.offset == 0x100

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


def test_fixed_records_text_truncates_oversize_entry(tmp_path):
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
    build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
          original_rom=rom_path)
    assert (tmp_path / "out.sfc").read_bytes()[0x1000:0x1005] == b"ABCDE"


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

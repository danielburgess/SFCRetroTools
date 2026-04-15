"""Phase 6b <fixed-records>: stride×count validator + raw-binary build/extract."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from retrotool.mbuild import (
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


def test_fixed_records_mbxml_attrs_parsed():
    spec = parse_mbxml_string(
        '<build><fixed-records file="t.bin" offset="100" stride="4" count="2"/></build>'
    )
    s = spec.sections[0]
    assert s.kind is SectionKind.FIXED_RECORDS
    assert s.stride == 4 and s.count == 2 and s.offset == 0x100


def test_fixed_records_toml_attrs_parsed():
    spec = parse_project_toml_dict({
        "mbuild": {
            "sections": [{
                "kind": "fixed-records", "file": "t.bin",
                "offset": "0x100", "stride": 4, "count": 2,
            }]
        }
    })
    s = spec.sections[0]
    assert s.kind is SectionKind.FIXED_RECORDS
    assert s.stride == 4 and s.count == 2 and s.offset == 0x100

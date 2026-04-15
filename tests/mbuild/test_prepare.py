"""Phase 6b prepare/apply split.

Pre-encodes <bin codec=>, <graphics>, <script> in `parallel_prepare()`,
verifies the handler picks up `_prepared` and skips re-encoding."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from retrotool.mbuild import BuildSpec, Section, SectionKind, build
from retrotool.mbuild.prepare import parallel_prepare, prepare_bin, prepare_script


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


def test_prepare_bin_with_codec(tmp_path):
    (tmp_path / "raw.bin").write_bytes(b"AAAAAAAA" * 16)
    s = Section(kind=SectionKind.BIN, offset=0x1000,
                files=[PurePosixPath("raw.bin")], codec="lzss-zamn")
    out = prepare_bin(s, tmp_path)
    assert isinstance(out, bytes)
    assert len(out) < len(b"AAAAAAAA" * 16)  # actually compressed


def test_prepare_script(tmp_path):
    tbl = tmp_path / "ascii.tbl"
    tbl.write_text("\n".join(f"{ord(c):02X}={c}" for c in "HI") + "\n",
                   encoding="utf-8")
    txt = tmp_path / "lines.txt"
    txt.write_text("HI\n", encoding="utf-8")
    s = Section(kind=SectionKind.SCRIPT, offset=0x100,
                files=[PurePosixPath("lines.txt")],
                table=PurePosixPath("ascii.tbl"))
    out = prepare_script(s, tmp_path)
    assert out == b"HI\x00"


def test_parallel_prepare_serial_mode_assigns_prepared(tmp_path):
    (tmp_path / "raw.bin").write_bytes(b"X" * 64)
    spec = BuildSpec(sections=[
        Section(kind=SectionKind.BIN, offset=0x1000,
                files=[PurePosixPath("raw.bin")], codec="lzss-zamn"),
        Section(kind=SectionKind.REP, offset=0x2000,
                files=[PurePosixPath("raw.bin")]),  # not prepare-eligible
    ])
    n = parallel_prepare(spec, tmp_path, max_workers=1)
    assert n == 1
    assert spec.sections[0]._prepared is not None
    assert spec.sections[1]._prepared is None


def test_build_with_parallel_serial_matches_non_parallel(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "raw.bin").write_bytes(b"X" * 64)
    spec_a = BuildSpec(sections=[Section(
        kind=SectionKind.BIN, offset=0x1000,
        files=[PurePosixPath("raw.bin")], codec="lzss-zamn",
    )])
    spec_b = BuildSpec(sections=[Section(
        kind=SectionKind.BIN, offset=0x1000,
        files=[PurePosixPath("raw.bin")], codec="lzss-zamn",
    )])
    out_a = tmp_path / "a.sfc"
    out_b = tmp_path / "b.sfc"
    build(spec_a, source_root=tmp_path, out_path=out_a, original_rom=rom_path)
    build(spec_b, source_root=tmp_path, out_path=out_b, original_rom=rom_path,
          parallel=1)
    assert out_a.read_bytes() == out_b.read_bytes()

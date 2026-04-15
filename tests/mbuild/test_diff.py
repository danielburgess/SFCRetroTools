"""Phase 4: IPS writer + xdelta3 wrapper.

IPS is pure python — full coverage. xdelta3 tests skip when binary absent."""
from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath

import pytest

from retrotool.mbuild import (
    BuildSpec,
    DiffError,
    Section,
    SectionKind,
    apply_ips,
    build,
    write_ips,
    write_xdelta,
    xdelta_available,
)
from retrotool.mbuild.diff import _encode_ips_runs, _pack_ips_record


_ROM_SIZE = 0x80_000


def _make_lorom(tmp_path: Path, marker: bytes = b"") -> Path:
    body = bytearray([0x00] * _ROM_SIZE)
    body[0x7FC0:0x7FC0 + 21] = b"TEST ROM             "
    body[0x7FD5] = 0x20
    body[0x7FD7] = 0x09
    if marker:
        body[0x100:0x100 + len(marker)] = marker
    body[0x7FDC:0x7FE0] = b"\xFF\xFF\x00\x00"
    csum = sum(body) & 0xFFFF
    body[0x7FDC] = (csum ^ 0xFFFF) & 0xFF
    body[0x7FDD] = ((csum ^ 0xFFFF) >> 8) & 0xFF
    body[0x7FDE] = csum & 0xFF
    body[0x7FDF] = (csum >> 8) & 0xFF
    p = tmp_path / f"rom_{marker.hex() or 'blank'}.sfc"
    p.write_bytes(body)
    return p


# ---- IPS unit tests -------------------------------------------------------


def test_ips_header_and_eof(tmp_path):
    original = b"\x00" * 16
    modified = b"\x00" * 16
    out = tmp_path / "noop.ips"
    write_ips(original, modified, out)
    data = out.read_bytes()
    assert data.startswith(b"PATCH")
    assert data.endswith(b"EOF")


def test_ips_single_byte_change_round_trip(tmp_path):
    original = b"\x00" * 32
    modified = bytearray(original)
    modified[5] = 0xAB
    out = tmp_path / "p.ips"
    write_ips(original, bytes(modified), out)
    assert apply_ips(out.read_bytes(), original) == bytes(modified)


def test_ips_multiple_runs_round_trip(tmp_path):
    original = bytes(range(256)) * 2
    modified = bytearray(original)
    modified[10:14] = b"\xFF\xFF\xFF\xFF"
    modified[100] = 0x77
    modified[200:210] = b"\xAA" * 10
    out = tmp_path / "p.ips"
    write_ips(original, bytes(modified), out)
    assert apply_ips(out.read_bytes(), original) == bytes(modified)


def test_ips_rle_compression_used(tmp_path):
    """A long run of identical new bytes should use a short RLE record,
    not a big raw one."""
    original = b"\x00" * 1024
    modified = bytearray(original)
    modified[100:100 + 500] = b"\xAA" * 500  # 500-byte run
    out = tmp_path / "p.ips"
    write_ips(original, bytes(modified), out)
    patch = out.read_bytes()
    # header(5) + offset(3) + len(2)=0 + rle_len(2) + value(1) + EOF(3) = 16
    assert len(patch) < 50, f"RLE run should compact tightly, got {len(patch)}b"
    assert apply_ips(patch, original) == bytes(modified)


def test_ips_rle_threshold_raw_kept_below_13(tmp_path):
    """A 12-byte run must stay raw (RLE threshold is 13)."""
    original = b"\x00" * 64
    modified = bytearray(original)
    modified[0:12] = b"\xAA" * 12
    out = tmp_path / "p.ips"
    write_ips(original, bytes(modified), out)
    # Raw record: 5 header bytes of record + 12 data bytes. Not the RLE 3-byte form.
    patch = out.read_bytes()
    assert apply_ips(patch, original) == bytes(modified)


def test_ips_growth_past_original_end(tmp_path):
    original = b"\x00" * 32
    modified = original + b"\xBE\xEF\xCA\xFE"
    out = tmp_path / "p.ips"
    write_ips(original, modified, out)
    assert apply_ips(out.read_bytes(), original) == modified


def test_ips_rejects_16mb_plus(tmp_path):
    with pytest.raises(DiffError, match="exceeds 16MB"):
        write_ips(b"", b"\x00" * (0x1000000 + 1), tmp_path / "big.ips")


def test_ips_runs_encoding():
    orig = b"\x00\x01\x02\x03\x04\x05"
    mod = b"\x00\xFF\xFF\x03\x04\x05"
    runs = _encode_ips_runs(orig, mod)
    assert runs == [(1, b"\xFF\xFF")]


# ---- build() integration --------------------------------------------------


def test_build_with_diff_ips_emits_sibling_patch(tmp_path):
    rom = _make_lorom(tmp_path)
    (tmp_path / "p.bin").write_bytes(b"\x11\x22\x33\x44")
    spec = BuildSpec(
        diff="ips",
        sections=[Section(kind=SectionKind.REP, offset=0x200,
                          files=[PurePosixPath("p.bin")])],
    )
    out = tmp_path / "built.sfc"
    result = build(spec, source_root=tmp_path, out_path=out, original_rom=rom)
    assert len(result.diffs) == 1
    d = result.diffs[0]
    assert d.format == "ips"
    assert d.path.exists()
    # Verify patch applies cleanly to the original.
    assert apply_ips(d.path.read_bytes(), rom.read_bytes()) == out.read_bytes()


def test_build_with_diff_both_emits_both(tmp_path):
    rom = _make_lorom(tmp_path)
    (tmp_path / "p.bin").write_bytes(b"\x99")
    spec = BuildSpec(
        diff="both",
        sections=[Section(kind=SectionKind.REP, offset=0x300,
                          files=[PurePosixPath("p.bin")])],
    )
    result = build(spec, source_root=tmp_path, out_path=tmp_path / "b.sfc",
                   original_rom=rom)
    formats = {d.format for d in result.diffs}
    assert formats == {"ips", "xdelta"}


# ---- xdelta3 --------------------------------------------------------------


def test_xdelta_graceful_skip_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr("retrotool.mbuild.diff._xdelta_bundled", lambda: None)
    d = write_xdelta(
        tmp_path / "orig.sfc", tmp_path / "mod.sfc", tmp_path / "out.xdelta",
        required=False,
    )
    assert d.skipped is True
    assert "xdelta3" in d.note


def test_xdelta_required_raises_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr("retrotool.mbuild.diff._xdelta_bundled", lambda: None)
    with pytest.raises(DiffError, match="xdelta3"):
        write_xdelta(tmp_path / "o.sfc", tmp_path / "m.sfc",
                     tmp_path / "x.xdelta", required=True)


@pytest.mark.skipif(not xdelta_available(), reason="xdelta3 binary not on PATH")
def test_xdelta_real_binary_round_trip(tmp_path):
    orig = _make_lorom(tmp_path, marker=b"ORIG")
    mod = _make_lorom(tmp_path, marker=b"MODIFIED")
    out = tmp_path / "delta.xdelta"
    d = write_xdelta(orig, mod, out, required=True)
    assert d.size > 0
    assert out.exists()

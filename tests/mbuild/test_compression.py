"""Phase 3: compression + bitplane handlers.

Covers <bin codec=…> build → extract round-trip via existing retrotool codecs
(lzss variants, rle), plus <graphics> raw passthrough and error-messaging for
unimplemented bitplane transforms."""
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


_ROM_SIZE = 0x80_000


def _make_lorom(tmp_path: Path) -> Path:
    body = bytearray([0x00] * _ROM_SIZE)
    body[0x7FC0:0x7FC0 + 21] = b"TEST ROM             "
    body[0x7FD5] = 0x20
    body[0x7FD7] = 0x09
    body[0x7FDC:0x7FE0] = b"\xFF\xFF\x00\x00"
    csum = sum(body) & 0xFFFF
    body[0x7FDC] = (csum ^ 0xFFFF) & 0xFF
    body[0x7FDD] = ((csum ^ 0xFFFF) >> 8) & 0xFF
    body[0x7FDE] = csum & 0xFF
    body[0x7FDF] = (csum >> 8) & 0xFF
    p = tmp_path / "base.sfc"
    p.write_bytes(body)
    return p


# ---- compression round-trips ---------------------------------------------

_PAYLOAD = bytes(range(256)) * 4  # 1 KB, compressible


@pytest.mark.parametrize("codec", ["lzss-zamn", "lzss-rbshura", "lzss-legacy", "rle"])
def test_bin_codec_round_trip(tmp_path, codec):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "src.bin").write_bytes(_PAYLOAD)

    build_spec = BuildSpec(sections=[Section(
        kind=SectionKind.BIN, offset=0x1000,
        files=[PurePosixPath("src.bin")],
        codec=codec, grow="insert",
    )])
    out = tmp_path / "out.sfc"
    build_result = build(build_spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    compressed_size = build_result.sections[0].write.length

    # Now decompress back. Codecs without self-terminating markers need `size`.
    (tmp_path / "src.bin").unlink()
    extract_spec = BuildSpec(sections=[Section(
        kind=SectionKind.BIN, offset=0x1000, size=compressed_size,
        files=[PurePosixPath("src.bin")],
        codec=codec,
    )])
    result = extract(extract_spec, source_root=tmp_path, original_rom=out)
    assert (tmp_path / "src.bin").read_bytes() == _PAYLOAD
    assert 0 < result.sections[0].bytes_read


def test_bin_unknown_codec_errors(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "p.bin").write_bytes(b"\x00")
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.BIN, offset=0x1000,
        files=[PurePosixPath("p.bin")], codec="nonsense-2077",
    )])
    with pytest.raises(HandlerError, match="unknown codec"):
        build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path)


def test_bin_compressed_fits_in_replace(tmp_path):
    """grow='replace' rejects overflow, accepts within-bounds compressed data."""
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "src.bin").write_bytes(b"\xAA" * 2048)  # highly compressible
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.BIN, offset=0x2000,
        files=[PurePosixPath("src.bin")],
        codec="rle", grow="replace",
    )])
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    # Must not have grown beyond original size.
    assert out.stat().st_size == _ROM_SIZE


# ---- graphics passthrough + transform error messages ----------------------


def test_graphics_raw_passthrough(tmp_path):
    rom_path = _make_lorom(tmp_path)
    tile_bytes = bytes(range(32))  # 32 bytes = one 4bpp tile
    (tmp_path / "tile.bin").write_bytes(tile_bytes)
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.GRAPHICS, offset=0x3000, bpp=4,
        files=[PurePosixPath("tile.bin")],
    )])
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    assert out.read_bytes()[0x3000:0x3020] == tile_bytes


def test_graphics_unknown_transform_errors(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "t.bin").write_bytes(b"\x00")
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.GRAPHICS, offset=0x3000,
        files=[PurePosixPath("t.bin")],
        codec="2bpp-to-1bpp-il",   # legacy MBuild bptype — not yet implemented
    )])
    with pytest.raises(HandlerError, match="not yet implemented"):
        build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path)


def test_graphics_round_trip_raw(tmp_path):
    rom_path = _make_lorom(tmp_path)
    tiles = bytes(range(128))  # 4 × 2bpp tiles
    (tmp_path / "tiles.bin").write_bytes(tiles)

    build_spec = BuildSpec(sections=[Section(
        kind=SectionKind.GRAPHICS, offset=0x4000, bpp=2,
        files=[PurePosixPath("tiles.bin")],
    )])
    out = tmp_path / "out.sfc"
    build(build_spec, source_root=tmp_path, out_path=out, original_rom=rom_path)

    (tmp_path / "tiles.bin").write_bytes(b"\x00" * len(tiles))  # placeholder for size
    extract_spec = BuildSpec(sections=[Section(
        kind=SectionKind.GRAPHICS, offset=0x4000, bpp=2,
        files=[PurePosixPath("tiles.bin")],
    )])
    extract(extract_spec, source_root=tmp_path, original_rom=out)
    assert (tmp_path / "tiles.bin").read_bytes() == tiles


# ---- mbxml integration (auto-migrated legacy elements) --------------------


def test_mbxml_lzr_auto_migrate_builds(tmp_path):
    """A legacy <lzr> element should parse, warn, and build via the codec
    registry — all in one go."""
    import warnings
    from retrotool.mbuild import parse_mbxml_string, MBXMLDeprecationWarning

    rom_path = _make_lorom(tmp_path)
    (tmp_path / "data.bin").write_bytes(b"\xCC" * 64)
    xml = f"""<build original="{rom_path.name}">
      <lzr file="data.bin" offset="5000" lztype="lzss-zamn"/>
    </build>"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MBXMLDeprecationWarning)
        spec = parse_mbxml_string(xml, source=str(tmp_path / "b.mbxml"))
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    # Round-trip decompress directly to verify bytes landed correctly.
    from retrotool.compression.registry import get as get_codec
    codec = get_codec("lzss-zamn")
    res = codec.decompress(out.read_bytes(), offset=0x5000)
    assert res.data == b"\xCC" * 64

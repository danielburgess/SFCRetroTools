"""Tests for retrotool.graphics.ppm.write_ppm."""
from __future__ import annotations

import pytest

from retrotool.graphics import write_ppm
from retrotool.graphics.ppm import _strip_alpha


def test_p6_binary_rgba_strips_alpha(tmp_path):
    # 2x1 image: red opaque, green opaque
    pixels = bytes([0xFF, 0x00, 0x00, 0xFF,    # R
                    0x00, 0xFF, 0x00, 0xFF])   # G
    out = tmp_path / "out.ppm"
    write_ppm(out, 2, 1, pixels)

    data = out.read_bytes()
    assert data.startswith(b"P6\n2 1\n255\n")
    body = data[len(b"P6\n2 1\n255\n"):]
    assert body == b"\xFF\x00\x00\x00\xFF\x00"


def test_p6_binary_rgb_passthrough(tmp_path):
    # Same image, but caller already stripped alpha.
    pixels = bytes([0xFF, 0x00, 0x00, 0x00, 0xFF, 0x00])
    out = tmp_path / "out.ppm"
    write_ppm(out, 2, 1, pixels, has_alpha=False)
    data = out.read_bytes()
    assert data == b"P6\n2 1\n255\n" + pixels


def test_p3_ascii_emits_text_triplets(tmp_path):
    pixels = bytes([255, 0, 0, 255,
                    0, 255, 0, 255,
                    0, 0, 255, 255])
    out = tmp_path / "out.ppm"
    write_ppm(out, 3, 1, pixels, binary=False)
    text = out.read_text(encoding="ascii")
    lines = text.rstrip("\n").split("\n")
    assert lines[0] == "P3"
    assert lines[1] == "3 1"
    assert lines[2] == "255"
    assert lines[3] == "255 0 0 0 255 0 0 0 255"


def test_p3_ascii_wraps_one_row_per_line(tmp_path):
    # 2x2 image; each row should land on its own ASCII line.
    pixels = bytes([
        10, 11, 12, 255,  20, 21, 22, 255,
        30, 31, 32, 255,  40, 41, 42, 255,
    ])
    out = tmp_path / "out.ppm"
    write_ppm(out, 2, 2, pixels, binary=False)
    lines = out.read_text(encoding="ascii").rstrip("\n").split("\n")
    assert lines[:3] == ["P3", "2 2", "255"]
    assert lines[3] == "10 11 12 20 21 22"
    assert lines[4] == "30 31 32 40 41 42"


def test_wrong_length_raises(tmp_path):
    out = tmp_path / "out.ppm"
    with pytest.raises(ValueError, match="pixels length"):
        write_ppm(out, 2, 1, bytes(7))   # need 8 bytes for 2x1 RGBA


def test_zero_dimension_raises(tmp_path):
    out = tmp_path / "out.ppm"
    with pytest.raises(ValueError, match="dimensions"):
        write_ppm(out, 0, 1, b"")
    with pytest.raises(ValueError, match="dimensions"):
        write_ppm(out, 1, -1, b"")


def test_round_trip_with_composite_to_image(tmp_path):
    # Integration: take whatever composite_to_image emits and verify the
    # (w, h, buf) triple feeds straight into write_ppm.
    from retrotool.graphics import Tile, composite_to_image
    from retrotool.graphics.palette import Palette

    pal = Palette(colors=[(0, 0, 0), (255, 255, 255),
                          (128, 128, 128), (200, 50, 50)])
    tile = Tile(pixels=[[i % 4 for i in range(8)] for _ in range(8)], bpp=2)
    w, h, buf = composite_to_image([tile], pal, cols=1)

    out = tmp_path / "tile.ppm"
    write_ppm(out, w, h, buf)
    data = out.read_bytes()
    assert data.startswith(f"P6\n{w} {h}\n255\n".encode("ascii"))
    body_len = len(data) - data.index(b"255\n") - 4
    assert body_len == w * h * 3


def test_strip_alpha_helper():
    rgba = bytes([1, 2, 3, 99, 4, 5, 6, 88])
    assert _strip_alpha(rgba) == b"\x01\x02\x03\x04\x05\x06"

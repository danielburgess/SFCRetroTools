"""PPM (Portable PixMap) image writer — P6 binary and P3 ASCII.

PPM is the simplest write-without-dependencies image format: a tiny ASCII
header followed by either raw RGB bytes (P6) or whitespace-separated ASCII
decimals (P3). No alpha channel; alpha is stripped on write.

This pairs naturally with `retrotool.graphics.tiles.composite_to_image`,
which returns `(width, height, rgba_bytes)` — pass the same triple
straight into `write_ppm`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union


def write_ppm(
    path: Union[str, Path],
    width: int,
    height: int,
    pixels: Union[bytes, bytearray, memoryview],
    *,
    binary: bool = True,
    has_alpha: bool = True,
) -> None:
    """Write a width × height image to `path` as PPM.

    Args:
        path: Output file path.
        width, height: Image dimensions in pixels. Both must be > 0.
        pixels: Pixel buffer in row-major top-to-bottom order. RGBA when
            `has_alpha=True` (4 bytes/pixel) or RGB when `has_alpha=False`
            (3 bytes/pixel).
        binary: When True, emit P6 (raw RGB binary). When False, emit P3
            (ASCII triplets — larger, useful for diffing / inspection).
        has_alpha: True if `pixels` is RGBA. The alpha channel is dropped
            on write since PPM has no alpha. False if `pixels` is already
            RGB.

    Raises:
        ValueError: dimensions are non-positive, or `pixels` length does
            not match width * height * (4 if has_alpha else 3).
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"PPM dimensions must be positive (got {width}x{height})")
    bpp = 4 if has_alpha else 3
    expected = width * height * bpp
    if len(pixels) != expected:
        raise ValueError(
            f"pixels length {len(pixels)} != width*height*{bpp} = {expected}"
        )

    rgb = _strip_alpha(pixels) if has_alpha else bytes(pixels)
    out_path = Path(path)
    if binary:
        header = f"P6\n{width} {height}\n255\n".encode("ascii")
        out_path.write_bytes(header + rgb)
    else:
        out_path.write_text(_to_p3_text(width, height, rgb), encoding="ascii")


def _strip_alpha(rgba: Union[bytes, bytearray, memoryview]) -> bytes:
    """RGBA → RGB. Drop every 4th byte."""
    out = bytearray((len(rgba) // 4) * 3)
    j = 0
    for i in range(0, len(rgba), 4):
        out[j] = rgba[i]
        out[j + 1] = rgba[i + 1]
        out[j + 2] = rgba[i + 2]
        j += 3
    return bytes(out)


def _to_p3_text(width: int, height: int, rgb: bytes) -> str:
    """Build the P3 ASCII body: header + one 'R G B' triplet per pixel,
    `width` triplets per line for readability."""
    lines = [f"P3\n{width} {height}\n255"]
    triplets_per_line = width
    pixel_count = (width * height)
    pos = 0
    for row_start in range(0, pixel_count, triplets_per_line):
        row = []
        for px in range(triplets_per_line):
            r = rgb[pos]; g = rgb[pos + 1]; b = rgb[pos + 2]
            row.append(f"{r} {g} {b}")
            pos += 3
        lines.append(" ".join(row))
    return "\n".join(lines) + "\n"

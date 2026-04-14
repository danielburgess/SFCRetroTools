"""SNES planar tile codec. 1BPP-IL, 2BPP, 4BPP, 8BPP. 8x8 tiles."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

TILE_W = 8
TILE_H = 8

# Bytes per 8x8 tile by bit depth
TILE_BYTES = {1: 8, 2: 16, 4: 32, 8: 64}


def decode_tile(data: bytes, offset: int, bpp: int) -> list[list[int]]:
    """Decode a single 8x8 planar tile → 8-row list of 8-col palette indices.

    SNES planar format: bitplanes stored as pairs.
      2BPP: 8 rows × 2 bytes (plane 0 + 1 interleaved per row)
      4BPP: first 16 bytes = planes 0/1, next 16 bytes = planes 2/3
      8BPP: 4 plane-pair groups × 16 bytes = 64 bytes
      1BPP-IL: 8 rows × 1 byte (single plane)
    """
    if bpp not in TILE_BYTES:
        raise ValueError(f"Unsupported bpp: {bpp}")
    if bpp == 1:
        return _decode_1bpp(data, offset)
    return _decode_planar_pairs(data, offset, bpp)


def _decode_1bpp(data: bytes, offset: int) -> list[list[int]]:
    out = [[0] * TILE_W for _ in range(TILE_H)]
    for y in range(TILE_H):
        b = data[offset + y]
        for x in range(TILE_W):
            out[y][x] = (b >> (7 - x)) & 1
    return out


def _decode_planar_pairs(data: bytes, offset: int, bpp: int) -> list[list[int]]:
    out = [[0] * TILE_W for _ in range(TILE_H)]
    pairs = bpp // 2                           # 1 for 2BPP, 2 for 4BPP, 4 for 8BPP
    for pair in range(pairs):
        base = offset + pair * 16              # each plane-pair = 16 bytes
        low_plane_bit = pair * 2
        for y in range(TILE_H):
            lo = data[base + y * 2]
            hi = data[base + y * 2 + 1]
            for x in range(TILE_W):
                bit = 7 - x
                v0 = (lo >> bit) & 1
                v1 = (hi >> bit) & 1
                out[y][x] |= (v0 << low_plane_bit) | (v1 << (low_plane_bit + 1))
    return out


def encode_tile(pixels: Sequence[Sequence[int]], bpp: int) -> bytes:
    """Inverse of decode_tile. `pixels` is 8x8 palette indices."""
    if bpp not in TILE_BYTES:
        raise ValueError(f"Unsupported bpp: {bpp}")
    if bpp == 1:
        return _encode_1bpp(pixels)
    return _encode_planar_pairs(pixels, bpp)


def _encode_1bpp(pixels: Sequence[Sequence[int]]) -> bytes:
    out = bytearray(TILE_H)
    for y in range(TILE_H):
        b = 0
        for x in range(TILE_W):
            b |= (pixels[y][x] & 1) << (7 - x)
        out[y] = b
    return bytes(out)


def _encode_planar_pairs(pixels: Sequence[Sequence[int]], bpp: int) -> bytes:
    pairs = bpp // 2
    out = bytearray(TILE_BYTES[bpp])
    for pair in range(pairs):
        base = pair * 16
        low_plane_bit = pair * 2
        for y in range(TILE_H):
            lo = hi = 0
            for x in range(TILE_W):
                px = pixels[y][x]
                v0 = (px >> low_plane_bit) & 1
                v1 = (px >> (low_plane_bit + 1)) & 1
                lo |= v0 << (7 - x)
                hi |= v1 << (7 - x)
            out[base + y * 2] = lo
            out[base + y * 2 + 1] = hi
    return bytes(out)


@dataclass
class Tile:
    pixels: list[list[int]]
    bpp: int

    @property
    def width(self) -> int:
        return TILE_W

    @property
    def height(self) -> int:
        return TILE_H

    @classmethod
    def decode(cls, data: bytes, offset: int, bpp: int) -> "Tile":
        return cls(pixels=decode_tile(data, offset, bpp), bpp=bpp)

    def encode(self) -> bytes:
        return encode_tile(self.pixels, self.bpp)

    def flipped(self, h: bool = False, v: bool = False) -> "Tile":
        px = self.pixels
        if h:
            px = [row[::-1] for row in px]
        if v:
            px = list(reversed(px))
        return Tile(pixels=[list(r) for r in px], bpp=self.bpp)


def decode_tiles(data: bytes, offset: int, count: int, bpp: int) -> list[Tile]:
    size = TILE_BYTES[bpp]
    return [Tile.decode(data, offset + i * size, bpp) for i in range(count)]


def tile_to_rgba(tile: Tile, palette) -> bytes:
    """Flatten tile pixels to RGBA byte buffer using palette.rgba(index)."""
    out = bytearray(TILE_W * TILE_H * 4)
    i = 0
    for row in tile.pixels:
        for idx in row:
            r, g, b, a = palette.rgba(idx)
            out[i] = r; out[i + 1] = g; out[i + 2] = b; out[i + 3] = a
            i += 4
    return bytes(out)


def composite_to_image(tiles: Iterable[Tile], palette, cols: int, padding_color=(0, 0, 0, 0)):
    """Return (width, height, rgba_bytes) grid rendering. Avoids PIL dependency."""
    tiles = list(tiles)
    if not tiles:
        return 0, 0, b''
    rows = (len(tiles) + cols - 1) // cols
    w = cols * TILE_W
    h = rows * TILE_H
    buf = bytearray(w * h * 4)
    pr, pg, pb, pa = palette_bg = (*padding_color,) if len(padding_color) == 4 else (*padding_color, 0)
    # Prefill with padding color
    for i in range(0, len(buf), 4):
        buf[i] = palette_bg[0]; buf[i + 1] = palette_bg[1]
        buf[i + 2] = palette_bg[2]; buf[i + 3] = palette_bg[3]
    for idx, tile in enumerate(tiles):
        tx = (idx % cols) * TILE_W
        ty = (idx // cols) * TILE_H
        for y in range(TILE_H):
            dst = ((ty + y) * w + tx) * 4
            for x in range(TILE_W):
                r, g, b, a = palette.rgba(tile.pixels[y][x])
                buf[dst] = r; buf[dst + 1] = g; buf[dst + 2] = b; buf[dst + 3] = a
                dst += 4
    return w, h, bytes(buf)

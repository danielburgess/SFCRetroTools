"""Tilemap entry decoding + metatile rendering.

SNES BG tilemap entry (16-bit word):
  bits 0-9   : tile index (0-1023)
  bits 10-12 : palette (0-7)
  bit  13    : priority
  bit  14    : H flip
  bit  15    : V flip
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from retrotool.graphics.tiles import TILE_H, TILE_W, Tile


@dataclass
class TilemapEntry:
    tile: int
    palette: int = 0
    priority: bool = False
    h_flip: bool = False
    v_flip: bool = False

    @classmethod
    def from_word(cls, word: int) -> "TilemapEntry":
        return cls(
            tile=word & 0x3FF,
            palette=(word >> 10) & 0x07,
            priority=bool(word & 0x2000),
            h_flip=bool(word & 0x4000),
            v_flip=bool(word & 0x8000),
        )

    def to_word(self) -> int:
        return (
            (self.tile & 0x3FF)
            | ((self.palette & 0x7) << 10)
            | (int(self.priority) << 13)
            | (int(self.h_flip) << 14)
            | (int(self.v_flip) << 15)
        )


def decode_tilemap(data: bytes, offset: int, width: int, height: int) -> list[list[TilemapEntry]]:
    out: list[list[TilemapEntry]] = []
    for y in range(height):
        row: list[TilemapEntry] = []
        for x in range(width):
            i = offset + (y * width + x) * 2
            word = data[i] | (data[i + 1] << 8)
            row.append(TilemapEntry.from_word(word))
        out.append(row)
    return out


def encode_tilemap(entries: Sequence[Sequence[TilemapEntry]]) -> bytes:
    out = bytearray()
    for row in entries:
        for e in row:
            w = e.to_word()
            out.append(w & 0xFF)
            out.append((w >> 8) & 0xFF)
    return bytes(out)


def render_tilemap(entries: Sequence[Sequence[TilemapEntry]], tiles: Sequence[Tile],
                   palettes: Sequence, transparent_if_priority: bool = False) -> tuple[int, int, bytes]:
    """Render a tilemap grid into an RGBA buffer. palettes is list of Palette objects."""
    if not entries:
        return 0, 0, b''
    h = len(entries)
    w = len(entries[0])
    out_w = w * TILE_W
    out_h = h * TILE_H
    buf = bytearray(out_w * out_h * 4)
    for ty, row in enumerate(entries):
        for tx, e in enumerate(row):
            tile = tiles[e.tile]
            if e.h_flip or e.v_flip:
                tile = tile.flipped(h=e.h_flip, v=e.v_flip)
            pal = palettes[e.palette] if e.palette < len(palettes) else palettes[0]
            for y in range(TILE_H):
                dst = ((ty * TILE_H + y) * out_w + tx * TILE_W) * 4
                for x in range(TILE_W):
                    r, g, b, a = pal.rgba(tile.pixels[y][x])
                    buf[dst] = r; buf[dst + 1] = g; buf[dst + 2] = b; buf[dst + 3] = a
                    dst += 4
    return out_w, out_h, bytes(buf)

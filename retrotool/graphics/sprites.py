"""Sprite composition + atlas packing.

A Sprite is a collection of positioned 8x8 tiles (or larger meta-objects)
referencing a shared palette. Common SNES OAM sizes: 8x8, 16x16, 32x32, 64x64.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from retrotool.graphics.tiles import TILE_H, TILE_W, Tile


@dataclass
class SpritePiece:
    """One 8x8 tile positioned within a sprite frame."""
    x: int
    y: int
    tile_index: int
    palette: int = 0
    h_flip: bool = False
    v_flip: bool = False
    priority: int = 0


@dataclass
class SpriteFrame:
    """A single frame composed of multiple 8x8 tiles."""
    pieces: list[SpritePiece] = field(default_factory=list)
    name: str = ""

    def bounds(self) -> tuple[int, int, int, int]:
        """Return (min_x, min_y, width, height) enclosing all pieces."""
        if not self.pieces:
            return 0, 0, 0, 0
        xs = [p.x for p in self.pieces]
        ys = [p.y for p in self.pieces]
        min_x, min_y = min(xs), min(ys)
        max_x = max(p.x + TILE_W for p in self.pieces)
        max_y = max(p.y + TILE_H for p in self.pieces)
        return min_x, min_y, max_x - min_x, max_y - min_y


def render_frame(frame: SpriteFrame, tiles: Sequence[Tile], palettes: Sequence) -> tuple[int, int, int, int, bytes]:
    """Render frame → (origin_x, origin_y, width, height, rgba). Origin is offset of (0,0) in buffer."""
    min_x, min_y, w, h = frame.bounds()
    if w == 0:
        return 0, 0, 0, 0, b''
    buf = bytearray(w * h * 4)
    for p in frame.pieces:
        tile = tiles[p.tile_index]
        if p.h_flip or p.v_flip:
            tile = tile.flipped(h=p.h_flip, v=p.v_flip)
        pal = palettes[p.palette] if p.palette < len(palettes) else palettes[0]
        px = p.x - min_x
        py = p.y - min_y
        for ty in range(TILE_H):
            dst = ((py + ty) * w + px) * 4
            for tx in range(TILE_W):
                r, g, b, a = pal.rgba(tile.pixels[ty][tx])
                if a == 0:
                    dst += 4
                    continue
                buf[dst] = r; buf[dst + 1] = g; buf[dst + 2] = b; buf[dst + 3] = a
                dst += 4
    return -min_x, -min_y, w, h, bytes(buf)


@dataclass
class AtlasEntry:
    name: str
    x: int
    y: int
    width: int
    height: int
    origin_x: int = 0
    origin_y: int = 0


@dataclass
class Atlas:
    width: int
    height: int
    rgba: bytes
    entries: list[AtlasEntry]


def pack_atlas(frames: Sequence[SpriteFrame], tiles: Sequence[Tile], palettes: Sequence,
               columns: int = 8, padding: int = 1) -> Atlas:
    """Simple row-packed atlas. Rows have uniform cell size per row."""
    rendered = []
    for f in frames:
        ox, oy, w, h, rgba = render_frame(f, tiles, palettes)
        rendered.append((f.name, ox, oy, w, h, rgba))

    # Greedy row packing
    entries: list[AtlasEntry] = []
    rows: list[list[tuple]] = []
    cur_row: list[tuple] = []
    for item in rendered:
        cur_row.append(item)
        if len(cur_row) == columns:
            rows.append(cur_row)
            cur_row = []
    if cur_row:
        rows.append(cur_row)

    row_heights = [max((h for _, _, _, _, h, _ in r), default=0) for r in rows]
    row_widths = [sum(w + padding for _, _, _, w, _, _ in r) - padding if r else 0 for r in rows]
    atlas_w = max(row_widths) if row_widths else 0
    atlas_h = sum(row_heights) + padding * (len(rows) - 1 if rows else 0)
    buf = bytearray(atlas_w * atlas_h * 4)

    y_cursor = 0
    for row, row_h in zip(rows, row_heights):
        x_cursor = 0
        for name, ox, oy, w, h, rgba in row:
            for yy in range(h):
                src = yy * w * 4
                dst = ((y_cursor + yy) * atlas_w + x_cursor) * 4
                buf[dst:dst + w * 4] = rgba[src:src + w * 4]
            entries.append(AtlasEntry(name=name, x=x_cursor, y=y_cursor,
                                      width=w, height=h, origin_x=ox, origin_y=oy))
            x_cursor += w + padding
        y_cursor += row_h + padding

    return Atlas(width=atlas_w, height=atlas_h, rgba=bytes(buf), entries=entries)

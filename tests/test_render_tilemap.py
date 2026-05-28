"""Tests for ``render_tilemap``'s ``transparent_if_priority`` mode.

Covers the SNES BG priority-bit isolation feature: when the flag is True,
entries with the priority bit set leave their 8x8 region fully transparent in
the output (RGBA 0,0,0,0), letting callers extract the non-priority background
layer as its own image.
"""
from __future__ import annotations

from retrotool.graphics.palette import Palette
from retrotool.graphics.tiles import TILE_H, TILE_W, Tile
from retrotool.graphics.tilemap import TilemapEntry, render_tilemap


def _solid_tile() -> Tile:
    """8x8 tile of all-index-1 pixels (non-transparent color)."""
    return Tile(pixels=[[1] * TILE_W for _ in range(TILE_H)], bpp=4)


def _red_palette() -> Palette:
    """Palette: index 0 = transparent black, index 1 = opaque red."""
    return Palette(
        colors=[(0, 0, 0), (255, 0, 0)],
        transparent_index=0,
    )


def _pixel(buf: bytes, x: int, y: int, width: int) -> tuple[int, int, int, int]:
    off = (y * width + x) * 4
    return buf[off], buf[off + 1], buf[off + 2], buf[off + 3]


def _tilemap_two_cells_one_priority() -> list[list[TilemapEntry]]:
    """1 row x 2 cells. Cell 0 has no priority; cell 1 has priority set."""
    return [[
        TilemapEntry(tile=0, palette=0, priority=False),
        TilemapEntry(tile=0, palette=0, priority=True),
    ]]


def test_render_tilemap_default_renders_priority_tiles_normally():
    """Default (flag False): the priority bit doesn't affect rendering."""
    w, h, buf = render_tilemap(
        _tilemap_two_cells_one_priority(),
        tiles=[_solid_tile()],
        palettes=[_red_palette()],
    )
    assert (w, h) == (TILE_W * 2, TILE_H)
    # Both cells must show opaque red across all their pixels.
    for cell_x in (0, TILE_W):
        for y in range(TILE_H):
            for x in range(TILE_W):
                assert _pixel(buf, cell_x + x, y, w) == (255, 0, 0, 255), (
                    f"cell @ x={cell_x}, pixel ({x},{y}) should be opaque red"
                )


def test_render_tilemap_transparent_if_priority_hides_priority_tiles():
    """Flag True: priority-bit cells leave their 8x8 region fully transparent;
    non-priority cells still render normally."""
    w, h, buf = render_tilemap(
        _tilemap_two_cells_one_priority(),
        tiles=[_solid_tile()],
        palettes=[_red_palette()],
        transparent_if_priority=True,
    )
    # Cell 0 (no priority) → opaque red everywhere.
    for y in range(TILE_H):
        for x in range(TILE_W):
            assert _pixel(buf, x, y, w) == (255, 0, 0, 255)
    # Cell 1 (priority) → fully transparent everywhere (RGBA 0,0,0,0).
    for y in range(TILE_H):
        for x in range(TILE_W):
            assert _pixel(buf, TILE_W + x, y, w) == (0, 0, 0, 0)


def test_render_tilemap_priority_skip_does_not_affect_other_cells_palette_alpha():
    """The skip-priority path must not disturb the palette's per-pixel alpha
    on non-priority cells (palette index 0 is transparent within those tiles)."""
    # Tile with a mix: top row index 0 (transparent via palette), rest index 1.
    mixed_pixels = [[0] * TILE_W] + [[1] * TILE_W for _ in range(TILE_H - 1)]
    tile = Tile(pixels=mixed_pixels, bpp=4)
    entries = [[TilemapEntry(tile=0, palette=0, priority=False)]]
    w, _h, buf = render_tilemap(
        entries, tiles=[tile], palettes=[_red_palette()],
        transparent_if_priority=True,
    )
    # Top row: palette index 0 -> alpha 0 (color RGB still present per Palette.rgba).
    for x in range(TILE_W):
        r, g, b, a = _pixel(buf, x, 0, w)
        assert a == 0, f"top-row pixel ({x},0) should have alpha 0 from palette"
    # Other rows: index 1 -> opaque red.
    for y in range(1, TILE_H):
        for x in range(TILE_W):
            assert _pixel(buf, x, y, w) == (255, 0, 0, 255)

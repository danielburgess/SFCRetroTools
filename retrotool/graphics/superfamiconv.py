"""SuperFamiconv wrapper — high-level PNG→(tiles, palette, map) conversion.

Binary resolved through `retrotool._toolchain.superfamiconv()` which prefers
the bundled `retrotool_libsfx` wheel, falls back to `$PATH`.

Usage:
    from retrotool.graphics import png_to_tiles, png_to_palette, png_to_map
    tiles = png_to_tiles("sprite.png", bpp=4)
"""
from __future__ import annotations

import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from retrotool._toolchain import ToolchainError, superfamiconv


# Back-compat alias. New code should import ToolchainError from retrotool.
SFCNotFoundError = ToolchainError


def sfc_run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Invoke SuperFamiconv with raw args. Pass-through to subprocess.run."""
    kwargs.setdefault("check", True)
    return subprocess.run([str(superfamiconv()), *args], **kwargs)


def _png_dimensions(png: str | Path) -> tuple[int, int]:
    """Read (width, height) from a PNG's IHDR — no image library needed."""
    with open(png, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise ValueError(f"{png}: not a PNG")
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def png_palette_rgb(png: str | Path) -> list[tuple[int, int, int]]:
    """Read an indexed PNG's PLTE chunk → list of (r,g,b), in palette-index
    order. Raises if the PNG has no PLTE (i.e. it isn't indexed-colour)."""
    with open(png, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{png}: not a PNG")
    i = 8
    while i + 8 <= len(data):
        ln = struct.unpack(">I", data[i:i + 4])[0]
        typ = data[i + 4:i + 8]
        if typ == b"PLTE":
            pl = data[i + 8:i + 8 + ln]
            return [(pl[j], pl[j + 1], pl[j + 2]) for j in range(0, ln - ln % 3, 3)]
        if typ == b"IDAT":
            break
        i += 12 + ln
    raise ValueError(f"{png}: no PLTE chunk (not an indexed-colour PNG?)")


def grouped_palette_bytes(
    colors_rgb: list[tuple[int, int, int]], *, subpalettes: int, colors_per: int
) -> bytes:
    """Build a fixed SNES palette (BGR555 bytes) from an ordered RGB list laid
    out as `[shared index-0] + (colors_per-1) colours per subpalette`.

    Preserves the source order so re-encoded tile pixel indices line up with a
    ROM's existing CGRAM (SuperFamiconv would otherwise re-sort colours and
    break the index↔CGRAM correspondence on reinsertion). `colors_rgb[0]` is the
    shared transparent/backdrop key used as index 0 of every subpalette.
    """
    shared = colors_rgb[0]
    rest = colors_rgb[1:]
    per = colors_per - 1
    out = bytearray()
    for k in range(subpalettes):
        grp = [shared] + rest[k * per:k * per + per]
        grp = (grp + [shared] * colors_per)[:colors_per]
        for (r, g, b) in grp:
            w = (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)
            out += bytes([w & 0xFF, (w >> 8) & 0xFF])
    return bytes(out)


def png_to_tiles(
    png: str | Path,
    bpp: int = 4,
    mode: str = "snes",
    no_flip: bool = False,
    no_discard: bool = False,
    palette: Optional[str | Path] = None,
    tile_width: int = 8,
    tile_height: int = 8,
) -> bytes:
    """Convert PNG to raw tile data. Returns tile bytes."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "tiles.bin"
        args = ["tiles", "-i", str(png), "-d", str(out), "-B", str(bpp), "-M", mode,
                "-W", str(tile_width), "-H", str(tile_height)]
        if no_flip:
            args.append("-F")
        if no_discard:
            args.append("-D")
        if palette:
            args += ["-p", str(palette)]
        sfc_run(args)
        return out.read_bytes()


def png_to_palette(
    png: str | Path,
    mode: str = "snes",
    colors: int = 16,
    palettes: int = 8,
    color_zero: Optional[str] = None,
    tile_width: int = 8,
    tile_height: int = 8,
) -> bytes:
    """Extract palette from PNG as raw bytes (BGR555 for snes mode).

    `color_zero` forces a specific colour (hex "RRGGBB") into index 0 of every
    subpalette — essential when a fixed backdrop/transparent key must map to the
    SNES transparent slot rather than being chosen by frequency.
    """
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "pal.bin"
        args = [
            "palette", "-i", str(png), "-d", str(out),
            "-M", mode, "-C", str(colors), "-P", str(palettes),
            "-W", str(tile_width), "-H", str(tile_height),
        ]
        if color_zero:
            args += ["-0", color_zero]
        sfc_run(args)
        return out.read_bytes()


def png_to_map(
    png: str | Path,
    tiles: str | Path,
    palette: str | Path,
    bpp: int = 4,
    mode: str = "snes",
    tile_width: int = 8,
    tile_height: int = 8,
) -> bytes:
    """Build tilemap referencing given tile+palette bins. Returns map bytes."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "map.bin"
        args = [
            "map", "-i", str(png), "-d", str(out),
            "-t", str(tiles), "-p", str(palette),
            "-B", str(bpp), "-M", mode,
            "-W", str(tile_width), "-H", str(tile_height),
        ]
        sfc_run(args)
        return out.read_bytes()


@dataclass
class EncodedGraphics:
    """One-call SuperFamiconv encode of a PNG into SNES-native graphics data.

    tiles    — raw planar tile bytes (deduplicated unless no_discard)
    palette  — raw BGR555 palette bytes (`colors` * `palettes` words)
    entries  — flat row-major list[TilemapEntry] (len == cols*rows), referencing
               `tiles`; .palette is the SuperFamiconv subpalette index
    cols/rows — tilemap dimensions (image size / tile size)
    """
    tiles: bytes
    palette: bytes
    entries: list  # list[TilemapEntry]
    cols: int
    rows: int

    def subpalette_colors(self, sub: int, count: int = 4) -> list[tuple[int, int, int]]:
        """RGB888 of the `count` colours in subpalette `sub` (for remapping)."""
        out = []
        for k in range(count):
            i = (sub * count + k) * 2
            w = self.palette[i] | (self.palette[i + 1] << 8)
            r = (w & 31) << 3; g = ((w >> 5) & 31) << 3; b = ((w >> 10) & 31) << 3
            out.append((r | r >> 5, g | g >> 5, b | b >> 5))
        return out


def encode_png(
    png: str | Path,
    *,
    bpp: int = 4,
    mode: str = "snes",
    colors: int = 16,
    palettes: int = 8,
    color_zero: Optional[str] = None,
    no_flip: bool = False,
    no_discard: bool = False,
    tile_width: int = 8,
    tile_height: int = 8,
    fixed_palette: Optional[bytes] = None,
) -> EncodedGraphics:
    """PNG -> (tiles, palette, tilemap entries) in one SuperFamiconv pass.

    The three SuperFamiconv stages are run with a shared palette so the returned
    tiles and map are mutually consistent. Use this as the reusable building
    block for "insert edited word-art / graphics back into a ROM" workflows;
    pair with `retrotool.graphics.project_tilemap` to place the entries into an
    engine-specific (sparse / windowed) destination tilemap.

    `fixed_palette` (raw BGR555 bytes) skips SuperFamiconv's palette extraction
    and packs against the supplied order instead — required when reinserting into
    a ROM whose CGRAM order is fixed, so pixel indices match (see
    `grouped_palette_bytes` / `png_palette_rgb`).
    """
    from retrotool.graphics.tilemap import TilemapEntry

    w, h = _png_dimensions(png)
    with tempfile.TemporaryDirectory() as td:
        pal_p = Path(td) / "pal.bin"
        til_p = Path(td) / "tiles.bin"
        if fixed_palette is not None:
            pal_bytes = fixed_palette
        else:
            pal_bytes = png_to_palette(
                png, mode=mode, colors=colors, palettes=palettes,
                color_zero=color_zero, tile_width=tile_width, tile_height=tile_height)
        pal_p.write_bytes(pal_bytes)
        tiles = png_to_tiles(
            png, bpp=bpp, mode=mode, no_flip=no_flip, no_discard=no_discard,
            palette=pal_p, tile_width=tile_width, tile_height=tile_height)
        til_p.write_bytes(tiles)
        map_bytes = png_to_map(
            png, tiles=til_p, palette=pal_p, bpp=bpp, mode=mode,
            tile_width=tile_width, tile_height=tile_height)

    entries = [TilemapEntry.from_word(map_bytes[i] | (map_bytes[i + 1] << 8))
               for i in range(0, len(map_bytes), 2)]
    return EncodedGraphics(
        tiles=tiles, palette=pal_bytes,
        entries=entries, cols=w // tile_width, rows=h // tile_height,
    )

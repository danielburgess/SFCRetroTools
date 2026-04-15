"""SuperFamiconv wrapper — high-level PNG→(tiles, palette, map) conversion.

Binary resolved through `retrotool._toolchain.superfamiconv()` which prefers
the bundled `retrotool_libsfx` wheel, falls back to `$PATH`.

Usage:
    from retrotool.graphics import png_to_tiles, png_to_palette, png_to_map
    tiles = png_to_tiles("sprite.png", bpp=4)
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from retrotool._toolchain import ToolchainError, superfamiconv


# Back-compat alias. New code should import ToolchainError from retrotool.
SFCNotFoundError = ToolchainError


def sfc_run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Invoke SuperFamiconv with raw args. Pass-through to subprocess.run."""
    kwargs.setdefault("check", True)
    return subprocess.run([str(superfamiconv()), *args], **kwargs)


def png_to_tiles(
    png: str | Path,
    bpp: int = 4,
    mode: str = "snes",
    no_flip: bool = False,
    no_discard: bool = False,
    palette: Optional[str | Path] = None,
) -> bytes:
    """Convert PNG to raw tile data. Returns tile bytes."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "tiles.bin"
        args = ["tiles", "-i", str(png), "-d", str(out), "-B", str(bpp), "-M", mode]
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
) -> bytes:
    """Extract palette from PNG as raw bytes (BGR555 for snes mode)."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "pal.bin"
        args = [
            "palette", "-i", str(png), "-d", str(out),
            "-M", mode, "-C", str(colors), "-P", str(palettes),
        ]
        sfc_run(args)
        return out.read_bytes()


def png_to_map(
    png: str | Path,
    tiles: str | Path,
    palette: str | Path,
    bpp: int = 4,
    mode: str = "snes",
) -> bytes:
    """Build tilemap referencing given tile+palette bins. Returns map bytes."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "map.bin"
        args = [
            "map", "-i", str(png), "-d", str(out),
            "-t", str(tiles), "-p", str(palette),
            "-B", str(bpp), "-M", mode,
        ]
        sfc_run(args)
        return out.read_bytes()

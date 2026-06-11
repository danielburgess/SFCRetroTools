"""Raw-byte and graphics handlers: rep / ins / bin (+codec) /
graphics (build-time PNG -> SNES tiles/tilemap)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from retrotool.build.spec import Section

from retrotool.build.handlers._base import (
    BuildContext,
    HandlerError,
    WriteRange,
    _attr_bool,
    _attr_hex,
    _load_callable,
    _read_concat,
    _resolve,
    _run_callable,
    _write,
)

def handle_rep(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    if section.offset is None:
        raise HandlerError(f"{section.source}: <rep> requires offset")
    data = _read_concat(section, root)
    return _write(rom, section.offset, data, allow_grow=False, source=section.source or "")


def handle_ins(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    if section.offset is None:
        raise HandlerError(f"{section.source}: <ins> requires offset")
    data = _read_concat(section, root)
    return _write(rom, section.offset, data, allow_grow=True, source=section.source or "")


def handle_bin(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    """Unified raw/compressed-bytes handler. Honors codec= and grow= (default 'replace')."""
    if section.offset is None:
        raise HandlerError(f"{section.source}: <bin> requires offset")
    data = _read_concat(section, root)

    if section.codec:
        from retrotool.compression import registry as codec_registry
        try:
            codec = codec_registry.get(section.codec)
        except KeyError as e:
            raise HandlerError(
                f"{section.source}: unknown codec={section.codec!r}. "
                f"Known: {codec_registry.list_schemes()}"
            ) from e
        result = codec.compress(data)
        data = result.data

    if section.size is not None and section.size != len(data):
        if len(data) > section.size:
            raise HandlerError(
                f"{section.source}: <bin> data ({len(data)}b) exceeds declared size ({section.size}b)"
            )
        data = data + b"\x00" * (section.size - len(data))

    # grow is validated once, at Section construction (spec.VALID_GROW).
    allow_grow = (section.grow or "replace").lower() == "insert"
    return _write(rom, section.offset, data, allow_grow=allow_grow, source=section.source or "")


def _handle_graphics_png(rom: bytearray, section: Section, root: Path) -> WriteRange:
    """PNG → SuperFamiconv tiles (+ optional projected tilemap), written into the
    ROM. Lets edited word-art / UI graphics round-trip back in at build time.

    Section attrs (all optional unless noted):
      file=          one .png (required)         offset=        tiles dest (required)
      bpp=2|4|8 (4)  color-zero=RRGGBB           no-flip=bool   tile-count=N (pad tiles)
      format=tiles|tilemap (auto: tilemap when map-offset set)
    Tilemap projection (format=tilemap):
      map-offset=    dest of the 16-bit entries (required)
      tile-base=     added to tile indices (VRAM tile slot the DMA targets)
      map-cols=      dest tilemap stride        (default 32)
      map-entries=   dest tilemap entry count   (default 1024)
      map-base-entry= dest entry of top-left cell (default 0)
      priority=bool  force priority bit
      palette-anchors= "P:RRGGBB,P:RRGGBB" — map each SuperFamiconv subpalette
                       (identified by which one contains the anchor colour) to
                       SNES palette number P. Omit → subpalette index used as-is.
    """
    from retrotool.graphics import (
        encode_png, grouped_palette_bytes, png_palette_rgb, project_tilemap,
    )

    if section.offset is None:
        raise HandlerError(f"{section.source}: <graphics> png requires offset")
    if len(section.files) != 1:
        raise HandlerError(f"{section.source}: <graphics> png requires exactly one file=")
    png = _resolve(Path(str(section.files[0])), root)
    if not png.exists():
        raise HandlerError(f"{section.source}: file not found: {png}")

    a = section.attrs
    bpp = _attr_hex(a.get("bpp")) or 4
    colors = _attr_hex(a.get("colors")) or (4 if bpp == 2 else 16)
    palettes = _attr_hex(a.get("palettes")) or 8
    no_flip = _attr_bool(a.get("no-flip"), key="no-flip",
                         source=section.source or "")
    # palette-from-png: pack against the indexed PNG's OWN palette order so tile
    # pixel indices line up with a ROM's fixed CGRAM (SuperFamiconv would
    # otherwise re-sort colours). PLTE laid out as [shared idx0] + (colors-1)
    # colours per subpalette; `palettes` selects how many subpalettes to take.
    fixed_palette = None
    if _attr_bool(a.get("palette-from-png"), key="palette-from-png",
                  source=section.source or ""):
        fixed_palette = grouped_palette_bytes(
            png_palette_rgb(png), subpalettes=palettes, colors_per=colors)
    enc = encode_png(png, bpp=bpp, colors=colors, palettes=palettes,
                     color_zero=a.get("color-zero"), no_flip=no_flip,
                     fixed_palette=fixed_palette)

    tile_bytes = bpp * 8
    tiles = enc.tiles
    tile_count = _attr_hex(a.get("tile-count"))
    if tile_count is not None:
        want = tile_count * tile_bytes
        if len(tiles) > want:
            raise HandlerError(
                f"{section.source}: {len(tiles)//tile_bytes} tiles exceed "
                f"tile-count={tile_count} (raise tile-count / the DMA budget, or "
                f"simplify the art / allow flips)")
        tiles = tiles.ljust(want, b"\x00")

    grow = (section.grow or "replace").lower()
    allow_grow = grow == "insert"
    written = [_write(rom, section.offset, tiles, allow_grow=allow_grow,
                      source=section.source or "")]

    map_off = _attr_hex(a.get("map-offset"))
    fmt = (a.get("format") or ("tilemap" if map_off is not None else "tiles")).lower()
    if fmt == "tilemap":
        if map_off is None:
            raise HandlerError(f"{section.source}: format=tilemap requires map-offset")
        # subpalette -> SNES palette via anchor colours
        palette_remap = None
        anchors = a.get("palette-anchors")
        if anchors:
            palette_remap = {}
            for pair in anchors.split(","):
                pnum, _, rgb = pair.strip().partition(":")
                rgb = rgb.strip().lstrip("#")
                target = (int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16))
                for sub in range(8):
                    if target in enc.subpalette_colors(sub):
                        palette_remap[sub] = int(pnum)
                        break
                else:
                    raise HandlerError(
                        f"{section.source}: palette-anchor {pair!r} colour not found "
                        f"in any subpalette")
        # blank source tiles (all pixels transparent) -> leave entry $0000
        skip_tiles = {i for i in range(len(enc.tiles) // tile_bytes)
                      if not any(enc.tiles[i * tile_bytes:(i + 1) * tile_bytes])}
        map_bytes = project_tilemap(
            enc.entries, enc.cols, enc.rows,
            tile_base=_attr_hex(a.get("tile-base")) or 0,
            base_entry=_attr_hex(a.get("map-base-entry")) or 0,
            dest_cols=_attr_hex(a.get("map-cols")) or 32,
            dest_entries=_attr_hex(a.get("map-entries")) or 1024,
            palette_remap=palette_remap,
            force_priority=_attr_bool(a.get("priority"), key="priority",
                                      source=section.source or ""),
            skip_tiles=skip_tiles,
        )
        written.append(_write(rom, map_off, map_bytes, allow_grow=allow_grow,
                              source=section.source or ""))
    return written if len(written) > 1 else written[0]


def handle_graphics(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    """Tile/palette/tilemap data — extensible to ANY graphics encoding.

    Modes (first match wins):
      * `encoder=` set — delegate to a project callable `path/mod.py:func` (or
        `pkg.mod:func`). The callable does ANY encoding (custom quantizers,
        metasprite tables, tilemap splices, or `retrotool.graphics.sfc_run(args)`
        for a fully-custom SuperFamiconv invocation) and writes its own bytes.
        Signature: `fn(rom, section, root, ctx) -> WriteRange | list[WriteRange]`.
      * `.png` file (or `format=`/`map-offset=` set) — SuperFamiconv encode →
        tiles + optional projected tilemap (see `_handle_graphics_png`).
      * raw planar binary — written through (identity bitplane only).
    """
    encoder = section.attrs.get("encoder")
    if encoder:
        return _run_callable(_load_callable(encoder, root, section.source or ""),
                             rom, section, root, ctx)
    if section.offset is None:
        raise HandlerError(f"{section.source}: <graphics> requires offset")
    is_png = bool(section.files) and str(section.files[0]).lower().endswith(".png")
    if is_png or section.attrs.get("format") or section.attrs.get("map-offset"):
        return _handle_graphics_png(rom, section, root)
    data = _read_concat(section, root)
    if not _is_identity_bitplane(section.codec):
        raise HandlerError(
            f"{section.source}: bitplane transform encode={section.codec!r} not yet "
            f"implemented (only raw passthrough supported)"
        )
    grow = (section.grow or "replace").lower()
    allow_grow = grow == "insert"
    return _write(rom, section.offset, data, allow_grow=allow_grow, source=section.source or "")


def _is_identity_bitplane(encode: Optional[str]) -> bool:
    return (encode or "").lower() in {"", "raw", "planar"}


def bitplane_reverse(encode: Optional[str]) -> Callable[[bytes], bytes]:
    """Resolve the reverse-direction bitplane transform (used by extract).

    Only identity passthrough is wired today; named transforms (e.g. MBuild's
    "2bpp-to-1bpp-il") raise until implemented.
    """
    if not _is_identity_bitplane(encode):
        raise HandlerError(f"bitplane transform encode={encode!r} not yet implemented")
    return lambda b: b



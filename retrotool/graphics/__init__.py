"""retrotool.graphics — tile/palette/sprite/tilemap codecs."""
from retrotool.graphics.palette import (
    Palette,
    bgr555_to_rgb888,
    decode_palette,
    encode_palette,
    rgb888_to_bgr555,
)
from retrotool.graphics.sprites import (
    Atlas,
    AtlasEntry,
    SpriteFrame,
    SpritePiece,
    pack_atlas,
    render_frame,
)
from retrotool.graphics.tilemap import (
    TilemapEntry,
    decode_tilemap,
    encode_tilemap,
    render_tilemap,
)
from retrotool.graphics.tiles import (
    TILE_BYTES,
    TILE_H,
    TILE_W,
    Tile,
    composite_to_image,
    decode_tile,
    decode_tiles,
    encode_tile,
    tile_to_rgba,
)

__all__ = [
    "Palette",
    "bgr555_to_rgb888",
    "rgb888_to_bgr555",
    "decode_palette",
    "encode_palette",
    "Tile",
    "TILE_W",
    "TILE_H",
    "TILE_BYTES",
    "decode_tile",
    "encode_tile",
    "decode_tiles",
    "tile_to_rgba",
    "composite_to_image",
    "TilemapEntry",
    "decode_tilemap",
    "encode_tilemap",
    "render_tilemap",
    "SpritePiece",
    "SpriteFrame",
    "render_frame",
    "Atlas",
    "AtlasEntry",
    "pack_atlas",
]

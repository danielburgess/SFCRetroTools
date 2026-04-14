"""Tiled TSX (tileset) XML emitter."""
from __future__ import annotations

from xml.etree import ElementTree as ET
from xml.dom import minidom


def build_tsx(name: str, image_path: str, image_width: int, image_height: int,
              tile_size: int = 8, columns: int = 32, tile_count: int | None = None) -> str:
    if tile_count is None:
        tile_count = (image_width // tile_size) * (image_height // tile_size)
    ts = ET.Element("tileset", {
        "version": "1.10",
        "name": name,
        "tilewidth": str(tile_size),
        "tileheight": str(tile_size),
        "tilecount": str(tile_count),
        "columns": str(columns),
    })
    ET.SubElement(ts, "image", {
        "source": image_path,
        "width": str(image_width),
        "height": str(image_height),
    })
    return minidom.parseString(ET.tostring(ts, encoding="utf-8")).toprettyxml(indent="  ")

"""Tiled TMX (tilemap) XML emitter."""
from __future__ import annotations

from xml.etree import ElementTree as ET
from xml.dom import minidom

from retrotool.extraction.level import Level


def _pretty(elem: ET.Element) -> str:
    return minidom.parseString(ET.tostring(elem, encoding="utf-8")).toprettyxml(indent="  ")


def build_tmx(level: Level, tileset_source: str = "tiles.tsx") -> str:
    map_el = ET.Element("map", {
        "version": "1.10",
        "orientation": "orthogonal",
        "renderorder": "right-down",
        "width": str(level.pixel_width // level.tile_size),
        "height": str(level.pixel_height // level.tile_size),
        "tilewidth": str(level.tile_size),
        "tileheight": str(level.tile_size),
        "infinite": "0",
    })
    ET.SubElement(map_el, "tileset", {"firstgid": "1", "source": tileset_source})

    for layer in level.layers:
        layer_el = ET.SubElement(map_el, "layer", {
            "name": layer.name,
            "width": str(layer.width),
            "height": str(layer.height),
        })
        data_el = ET.SubElement(layer_el, "data", {"encoding": "csv"})
        rows = [",".join(str(t + 1) for t in row) for row in layer.tile_indices]
        data_el.text = "\n" + ",\n".join(rows) + "\n"

    if level.triggers or level.spawns:
        og = ET.SubElement(map_el, "objectgroup", {"name": "objects"})
        oid = 1
        for tr in level.triggers:
            obj = ET.SubElement(og, "object", {
                "id": str(oid), "type": tr.kind,
                "x": str(tr.x), "y": str(tr.y),
                "width": str(tr.width), "height": str(tr.height),
            })
            oid += 1
            if tr.properties:
                props = ET.SubElement(obj, "properties")
                for k, v in tr.properties.items():
                    ET.SubElement(props, "property", {"name": k, "value": str(v)})
        for sp in level.spawns:
            obj = ET.SubElement(og, "object", {
                "id": str(oid), "type": f"spawn_{sp.entity_id}",
                "x": str(sp.x), "y": str(sp.y),
            })
            oid += 1
            props = ET.SubElement(obj, "properties")
            ET.SubElement(props, "property", {"name": "entity_id", "value": str(sp.entity_id)})
            if sp.delay:
                ET.SubElement(props, "property", {"name": "delay", "value": str(sp.delay)})
            ET.SubElement(props, "property", {"name": "respawn",
                                              "type": "bool",
                                              "value": "true" if sp.respawn else "false"})
            for k, v in sp.properties.items():
                ET.SubElement(props, "property", {"name": k, "value": str(v)})

    return _pretty(map_el)

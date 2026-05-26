"""Godot TileSet resource builder. Atlas sources + physics/collision layers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from retrotool.export.godot.resource import GdResource


@dataclass
class TileAtlas:
    texture_path: str                # res:// path
    tile_size: tuple[int, int] = (8, 8)
    columns: int = 32
    count: int = 0


@dataclass
class PhysicsLayerSpec:
    collision_layer: int = 1
    collision_mask: int = 1


def build_tileset(
    atlases: list[TileAtlas],
    physics_layers: list[PhysicsLayerSpec] | None = None,
    extra_properties: dict[str, Any] | None = None,
) -> GdResource:
    """Produce a .tres TileSet with atlas sources + physics layers."""
    ext_resources = []
    sub_resources = []
    physics_layers = physics_layers or []

    for i, atlas in enumerate(atlases, start=1):
        ext_id = f'"Atlas_{i}"'
        ext_resources.append({
            "type": "Texture2D",
            "path": atlas.texture_path,
            "id": ext_id,
        })
        sub_resources.append({
            "type": "TileSetAtlasSource",
            "id": f'"Src_{i}"',
            "properties": {
                "texture": f"ExtResource({ext_id})",
                "texture_region_size": atlas.tile_size,
            },
        })

    resource = GdResource(
        resource_type="TileSet",
        load_steps=1 + len(atlases) * 2,
        ext_resources=ext_resources,
        sub_resources=sub_resources,
        properties={},
    )
    for i, atlas in enumerate(atlases, start=1):
        resource.properties[f"sources/{i}"] = f"SubResource(\"Src_{i}\")"
    for i, layer in enumerate(physics_layers):
        resource.properties[f"physics_layer_{i}/collision_layer"] = layer.collision_layer
        resource.properties[f"physics_layer_{i}/collision_mask"] = layer.collision_mask
    if extra_properties:
        resource.properties.update(extra_properties)
    return resource

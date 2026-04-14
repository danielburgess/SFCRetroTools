"""SpriteFrames resource builder (animations → Godot AnimatedSprite2D data)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from retrotool.export.godot.resource import GdResource


@dataclass
class Animation:
    name: str
    frames: list[dict] = field(default_factory=list)     # each: {"texture": res_id, "duration": float}
    loop: bool = True
    speed: float = 10.0


def build_sprite_frames(animations: list[Animation], ext_textures: dict[str, str]) -> GdResource:
    """ext_textures: map from atlas name → res:// path."""
    ext_resources = [
        {"type": "Texture2D", "path": path, "id": f'"{name}"'}
        for name, path in ext_textures.items()
    ]
    properties: dict[str, Any] = {"animations": []}
    anims_list = []
    for a in animations:
        frames_list = [
            {"texture": f"ExtResource({f['texture']})", "duration": f.get("duration", 1.0)}
            for f in a.frames
        ]
        anims_list.append({
            "name": f'"{a.name}"',
            "frames": frames_list,
            "loop": a.loop,
            "speed": a.speed,
        })
    properties["animations"] = anims_list
    return GdResource(
        resource_type="SpriteFrames",
        load_steps=1 + len(ext_resources),
        ext_resources=ext_resources,
        properties=properties,
    )

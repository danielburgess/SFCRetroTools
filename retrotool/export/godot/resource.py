"""Godot .tres text resource serialization."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def format_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        escaped = v.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, tuple) and len(v) == 2:
        return f"Vector2({v[0]}, {v[1]})"
    if isinstance(v, list):
        return "[" + ", ".join(format_value(x) for x in v) + "]"
    if isinstance(v, dict):
        items = ", ".join(f"{format_value(k)}: {format_value(val)}" for k, val in v.items())
        return "{" + items + "}"
    return repr(v)


@dataclass
class GdResource:
    """A Godot resource block. Headers + typed sub-resources + properties."""
    resource_type: str               # e.g. 'TileSet', 'SpriteFrames'
    load_steps: int = 1
    format: int = 3
    sub_resources: list[dict] = field(default_factory=list)
    ext_resources: list[dict] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        out: list[str] = []
        out.append(
            f'[gd_resource type="{self.resource_type}" load_steps={self.load_steps} format={self.format}]\n'
        )
        for ext in self.ext_resources:
            fields = " ".join(f'{k}={format_value(v)}' for k, v in ext.items())
            out.append(f'[ext_resource {fields}]')
        if self.ext_resources:
            out.append("")
        for sub in self.sub_resources:
            header_fields = {k: v for k, v in sub.items() if k != "properties"}
            fields = " ".join(f'{k}={format_value(v)}' for k, v in header_fields.items())
            out.append(f'[sub_resource {fields}]')
            for k, v in (sub.get("properties") or {}).items():
                out.append(f"{k} = {format_value(v)}")
            out.append("")
        out.append("[resource]")
        for k, v in self.properties.items():
            out.append(f"{k} = {format_value(v)}")
        return '\n'.join(out) + '\n'

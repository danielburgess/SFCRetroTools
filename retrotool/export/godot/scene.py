"""Godot .tscn scene serialization."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from retrotool.export.godot.resource import format_value


@dataclass
class GdNode:
    name: str
    type: str
    parent: str = "."                # '.' for root, or 'Parent/Path'
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class GdScene:
    root_name: str
    root_type: str = "Node2D"
    ext_resources: list[dict] = field(default_factory=list)
    sub_resources: list[dict] = field(default_factory=list)
    nodes: list[GdNode] = field(default_factory=list)
    root_properties: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        load_steps = 1 + len(self.ext_resources) + len(self.sub_resources)
        out = [f'[gd_scene load_steps={load_steps} format=3]\n']

        for ext in self.ext_resources:
            fields = " ".join(f'{k}={format_value(v)}' for k, v in ext.items())
            out.append(f'[ext_resource {fields}]')
        if self.ext_resources:
            out.append("")

        for sub in self.sub_resources:
            header = {k: v for k, v in sub.items() if k != "properties"}
            fields = " ".join(f'{k}={format_value(v)}' for k, v in header.items())
            out.append(f'[sub_resource {fields}]')
            for k, v in (sub.get("properties") or {}).items():
                out.append(f"{k} = {format_value(v)}")
            out.append("")

        out.append(f'[node name="{self.root_name}" type="{self.root_type}"]')
        for k, v in self.root_properties.items():
            out.append(f"{k} = {format_value(v)}")
        out.append("")

        for n in self.nodes:
            parent = f' parent="{n.parent}"' if n.parent != "." else ""
            out.append(f'[node name="{n.name}" type="{n.type}"{parent}]')
            for k, v in n.properties.items():
                out.append(f"{k} = {format_value(v)}")
            out.append("")

        return '\n'.join(out) + '\n'

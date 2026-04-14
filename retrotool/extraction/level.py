"""Level map extraction. Tiles, collision, triggers, spawns, palette zones."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TileLayer:
    name: str
    width: int
    height: int
    tile_indices: list[list[int]]
    priority: bool = False
    palette: int = 0


@dataclass
class CollisionCell:
    """One cell of collision data. Semantic kind + optional shape polygon."""
    kind: str                       # solid|water|hazard|slope|ladder|air|...
    shape: list[tuple[int, int]] = field(default_factory=list)   # polygon points in tile-local pixel coords


@dataclass
class CollisionLayer:
    width: int
    height: int
    cells: list[list[CollisionCell]]


@dataclass
class Trigger:
    x: int
    y: int
    width: int
    height: int
    kind: str                       # door|warp|event|cutscene|...
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpawnPoint:
    x: int
    y: int
    entity_id: int
    delay: int = 0
    respawn: bool = False
    area_min: tuple[int, int] | None = None
    area_max: tuple[int, int] | None = None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaletteZone:
    rect: tuple[int, int, int, int]   # x, y, w, h in tile units
    palette_id: int
    animated: bool = False


@dataclass
class Level:
    name: str
    pixel_width: int
    pixel_height: int
    tile_size: int = 8
    layers: list[TileLayer] = field(default_factory=list)
    collision: CollisionLayer | None = None
    triggers: list[Trigger] = field(default_factory=list)
    spawns: list[SpawnPoint] = field(default_factory=list)
    palette_zones: list[PaletteZone] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

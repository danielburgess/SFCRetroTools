"""Entity/object registry. Models game entity definitions."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EntityDef:
    id: int
    name: str
    category: str = "unknown"        # enemy|npc|pickup|hazard|decoration|...
    sprite_set: str = ""
    animations: list[str] = field(default_factory=list)
    default_palette: int = 0
    behavior: str = ""               # name of behavior script
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityRegistry:
    entities: dict[int, EntityDef] = field(default_factory=dict)

    def add(self, entity: EntityDef) -> None:
        self.entities[entity.id] = entity

    def get(self, entity_id: int) -> EntityDef | None:
        return self.entities.get(entity_id)

    def by_category(self, category: str) -> list[EntityDef]:
        return [e for e in self.entities.values() if e.category == category]

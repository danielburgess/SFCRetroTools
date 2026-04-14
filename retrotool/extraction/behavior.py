"""Behavior extraction scaffolding. Captures state machines from disasm patterns."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BehaviorState:
    name: str
    entry_addr: int                  # SNES address of handler
    transitions: dict[str, str] = field(default_factory=dict)   # event_name → state_name
    actions: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class Behavior:
    name: str
    initial_state: str = "idle"
    states: dict[str, BehaviorState] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)

    def add_state(self, state: BehaviorState) -> None:
        self.states[state.name] = state

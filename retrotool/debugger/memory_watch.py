"""Memory diffing + watch patterns over IPC."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from retrotool.debugger.client import MesenClient


@dataclass
class MemoryRegion:
    memory_type: str
    address: int
    length: int

    def read(self, client: MesenClient) -> bytes:
        return client.read_memory(self.memory_type, self.address, self.length)


def diff_bytes(a: bytes, b: bytes) -> list[tuple[int, int, int]]:
    """Return list of (offset, old, new) for every byte that changed."""
    return [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]


def watch(client: MesenClient, region: MemoryRegion, iterations: int,
          step_between: int = 1, on_change: Callable[[int, int, int], None] | None = None) -> list[tuple[int, int, int]]:
    """Step emulator N times, diff region each tick. Return aggregated changes."""
    prev = region.read(client)
    all_changes: list[tuple[int, int, int]] = []
    for _ in range(iterations):
        client.step(step_between)
        cur = region.read(client)
        changes = diff_bytes(prev, cur)
        for c in changes:
            if on_change:
                on_change(*c)
            all_changes.append(c)
        prev = cur
    return all_changes

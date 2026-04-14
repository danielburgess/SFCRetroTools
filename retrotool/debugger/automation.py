"""High-level automation patterns over IPC."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from retrotool.debugger.client import MesenClient


@contextmanager
def paused(client: MesenClient) -> Iterator[None]:
    """Context manager: pause emulator during block, resume on exit."""
    status = client.get_status()
    was_paused = bool(status.get("paused"))
    if not was_paused:
        client.pause()
    try:
        yield
    finally:
        if not was_paused:
            client.resume()


def run_until_breakpoint(client: MesenClient, bp_addr: int, memory_type: str = "SnesPrgRom",
                         timeout_steps: int = 10_000_000) -> bool:
    """Add a one-shot breakpoint, resume, poll until hit or timeout."""
    bp_id = client.add_breakpoint(bp_addr, memory_type, "exec")
    try:
        client.resume()
        for _ in range(timeout_steps):
            status = client.get_status()
            if status.get("paused"):
                pc = client.get_cpu_state().get("pc")
                if pc == bp_addr:
                    return True
        return False
    finally:
        client.remove_breakpoint(bp_id)


def snapshot_registers(client: MesenClient) -> dict:
    with paused(client):
        return client.get_cpu_state()

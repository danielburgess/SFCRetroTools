"""Mesen IPC command-sequence shapes (IpcStep / IpcPlan).

Structured plan dataclasses external scripts can hand to either an LLM (to fill in step
parameters) or a `MesenClient` (to apply step-by-step). No IPC traffic happens here.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IpcStep:
    command: str
    params: dict
    why: str = ""


@dataclass
class IpcPlan:
    goal: str
    steps: list[IpcStep] = field(default_factory=list)

    def to_json(self) -> str:
        import json
        return json.dumps({
            "goal": self.goal,
            "steps": [{"command": s.command, "params": s.params, "why": s.why} for s in self.steps],
        }, indent=2)


def find_text_render_routine(rom_name: str, text_ram_addr: int) -> IpcPlan:
    """Canned plan: locate code that writes to text-RAM area."""
    return IpcPlan(
        goal=f"Find the routine that renders text into RAM at {text_ram_addr:#06X} ({rom_name}).",
        steps=[
            IpcStep("addBreakpoint", {"address": text_ram_addr, "memoryType": "SnesWorkRam", "type": "write"},
                    "Trigger on any write to text area."),
            IpcStep("resume", {}, "Let the game run so text renders."),
            IpcStep("getStatus", {}, "Poll until breakpoint hits; paused=true signals success."),
            IpcStep("getCpuState", {}, "Capture PC at hit — candidate render routine entry."),
            IpcStep("getCallstack", {}, "Recover callers for context."),
        ],
    )

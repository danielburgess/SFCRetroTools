"""Multi-stage extraction orchestrator."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class PipelineStage:
    name: str
    run: Callable[[dict], dict]      # (context) → updated context
    depends_on: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self):
        self.stages: list[PipelineStage] = []

    def add(self, stage: PipelineStage) -> "Pipeline":
        self.stages.append(stage)
        return self

    def run(self, initial_context: dict | None = None) -> dict:
        context = dict(initial_context or {})
        completed: set[str] = set()
        remaining = list(self.stages)
        while remaining:
            progressed = False
            for stage in list(remaining):
                if set(stage.depends_on).issubset(completed):
                    context = stage.run(context) or context
                    completed.add(stage.name)
                    remaining.remove(stage)
                    progressed = True
            if not progressed:
                unmet = {s.name: s.depends_on for s in remaining}
                raise RuntimeError(f"Pipeline has unmet dependencies: {unmet}")
        return context

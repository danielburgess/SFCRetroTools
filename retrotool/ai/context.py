"""ProjectConfig → ProjectContext shape for use as an LLM prompt prelude.

Pure data transform. External callers send `ProjectContext.to_prompt()` to their model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from retrotool.project.schema import ProjectConfig


@dataclass
class ProjectContext:
    """Compact summary of a project for LLM consumption."""
    name: str
    mapping: str
    rom_size: int
    data_defs: list[str] = field(default_factory=list)
    known_schemes: list[str] = field(default_factory=list)
    notes: str = ""

    def to_prompt(self) -> str:
        lines = [
            f"Project: {self.name}",
            f"Mapping: {self.mapping}, size: {self.rom_size:#x} bytes",
            f"Compression schemes in use: {', '.join(self.known_schemes) or 'unknown'}",
            f"Data definitions: {', '.join(self.data_defs) or '(none yet)'}",
        ]
        if self.notes:
            lines.append("Notes:")
            lines.append(self.notes)
        return '\n'.join(lines)


def build_context(project: ProjectConfig, data_def_names: list[str] | None = None,
                  compression_schemes: list[str] | None = None) -> ProjectContext:
    return ProjectContext(
        name=project.rom.name,
        mapping=project.rom.mapping,
        rom_size=project.rom.size,
        data_defs=data_def_names or [],
        known_schemes=compression_schemes or [],
    )

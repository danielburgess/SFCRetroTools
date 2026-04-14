"""retrotool.ai — LLM-assisted RE workflows."""
from retrotool.ai.context import ProjectContext, build_context
from retrotool.ai.ipc_prompt import IpcPlan, IpcStep, find_text_render_routine
from retrotool.ai.prompts import (
    DISCOVER_LEVEL_FORMAT,
    IDENTIFY_COMPRESSION,
    LOCATE_TEXT_TABLE,
    SUGGEST_ASAR_HOOK,
)
from retrotool.ai.workflows import (
    DISCOVER_TEXT_SYSTEM_WORKFLOW,
    IDENTIFY_COMPRESSION_WORKFLOW,
    WorkflowStep,
)

__all__ = [
    "ProjectContext",
    "build_context",
    "IpcPlan",
    "IpcStep",
    "find_text_render_routine",
    "IDENTIFY_COMPRESSION",
    "LOCATE_TEXT_TABLE",
    "DISCOVER_LEVEL_FORMAT",
    "SUGGEST_ASAR_HOOK",
    "WorkflowStep",
    "IDENTIFY_COMPRESSION_WORKFLOW",
    "DISCOVER_TEXT_SYSTEM_WORKFLOW",
]

"""retrotool.ai — prompt templates and dataclass shapes for external LLM-driven scripts.

This package does NOT call any LLM. It is a vocabulary — `str.format`-style prompt
templates plus structured plan/workflow dataclasses — for downstream scripts that wire
their own model client. See README §retrotool.ai for the intended usage pattern.
"""
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

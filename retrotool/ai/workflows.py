"""Canned `WorkflowStep` sequences for external LLM-driven RE orchestrators.

Each step is a static prompt + expected_output pair; this module provides no executor.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkflowStep:
    name: str
    prompt: str
    expected_output: str = ""
    depends_on: list[str] = field(default_factory=list)


IDENTIFY_COMPRESSION_WORKFLOW = [
    WorkflowStep(
        name="scan_candidates",
        prompt="Scan ROM for high-entropy blocks that plausibly decompress under each known LZSS preset.",
        expected_output="List of (offset, scheme, consumed, decompressed_size) tuples.",
    ),
    WorkflowStep(
        name="classify_best",
        prompt="For top candidates, propose the most likely scheme and params.",
        expected_output="JSON per candidate: {offset, scheme, params}.",
        depends_on=["scan_candidates"],
    ),
    WorkflowStep(
        name="trial_decompress",
        prompt="Decompress with proposed params; verify by entropy + byte-plane structure of output.",
        expected_output="Success/failure verdict per candidate with reasons.",
        depends_on=["classify_best"],
    ),
]


DISCOVER_TEXT_SYSTEM_WORKFLOW = [
    WorkflowStep(
        name="scan_text",
        prompt="Find printable-ASCII-ish runs in ROM.",
        expected_output="List of TextBlock candidates.",
    ),
    WorkflowStep(
        name="find_pointer_tables",
        prompt="Scan for pointer tables whose targets land in the text runs.",
        expected_output="Pointer table candidates referencing text blocks.",
        depends_on=["scan_text"],
    ),
    WorkflowStep(
        name="build_table_file",
        prompt="Propose a .tbl mapping using observed glyphs and a sample of decoded strings.",
        expected_output=".tbl content + sample decoded strings.",
        depends_on=["find_pointer_tables"],
    ),
]

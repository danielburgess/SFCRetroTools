"""retrotool.script — table codec, extraction, insertion, DTE, validation."""
from retrotool.script.dte import apply_dte, build_dte_table, find_digraphs, savings_estimate
from retrotool.script.extractor import Script, ScriptEntry, extract_script
from retrotool.script.inserter import InsertedScript, compile_script
from retrotool.script.table import Table
from retrotool.script.validator import ValidationReport, check_max_length, check_pointer_range, round_trip

__all__ = [
    "Table",
    "Script",
    "ScriptEntry",
    "extract_script",
    "InsertedScript",
    "compile_script",
    "find_digraphs",
    "build_dte_table",
    "apply_dte",
    "savings_estimate",
    "ValidationReport",
    "round_trip",
    "check_max_length",
    "check_pointer_range",
]

"""retrotool.debugger — Mesen2-Diz IPC integration."""
from retrotool.debugger.automation import paused, run_until_breakpoint, snapshot_registers
from retrotool.debugger.breakpoints import (
    Breakpoint,
    BreakpointError,
    make_mesen_breakpoints,
    parse_symfile,
    read_breakpoints,
    to_mesen_calls,
)
from retrotool.debugger.client import (
    DEFAULT_PIPE_NAME,
    IpcError,
    IpcResponse,
    MesenClient,
    derive_pipe_name,
)
from retrotool.debugger.memory_watch import MemoryRegion, diff_bytes, watch

__all__ = [
    "MesenClient",
    "IpcError",
    "IpcResponse",
    "DEFAULT_PIPE_NAME",
    "derive_pipe_name",
    "MemoryRegion",
    "diff_bytes",
    "watch",
    "paused",
    "run_until_breakpoint",
    "snapshot_registers",
    "Breakpoint",
    "BreakpointError",
    "make_mesen_breakpoints",
    "parse_symfile",
    "read_breakpoints",
    "to_mesen_calls",
]

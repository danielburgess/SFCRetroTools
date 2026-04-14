"""Mesen2-Diz IPC client. Unix/Windows named pipe, newline-delimited JSON."""
from __future__ import annotations

import json
import os
import platform
import re
import socket
from dataclasses import dataclass
from typing import Any, Optional

DEFAULT_PIPE_NAME = "Mesen2Diz_DebuggerIpc"


class IpcError(RuntimeError):
    pass


def derive_pipe_name(rom_name: str) -> str:
    """Sanitize rom name → valid pipe name (matches C# side)."""
    sanitized = re.sub(r"[^A-Za-z0-9_]", "", rom_name)
    return f"Mesen2Diz_{sanitized}" if sanitized else DEFAULT_PIPE_NAME


def _pipe_path(pipe_name: str) -> str:
    if platform.system() == "Windows":
        return fr"\\.\pipe\{pipe_name}"
    return f"/tmp/CoreFxPipe_{pipe_name}"


@dataclass
class IpcResponse:
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class MesenClient:
    """Minimal newline-JSON client. Reconnect-safe, synchronous request/response."""

    def __init__(self, pipe_name: str = DEFAULT_PIPE_NAME, timeout: float = 5.0):
        self.pipe_name = pipe_name
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._buf = bytearray()

    # --- connection ------------------------------------------------------
    def connect(self) -> None:
        if self._sock is not None:
            return
        path = _pipe_path(self.pipe_name)
        if platform.system() == "Windows":
            # On Windows the CoreFxPipe bridge may need a different transport;
            # prefer the win32 API via pywin32 in real deployments. Here we raise
            # so callers get a clear message instead of a silent hang.
            raise IpcError("Windows named-pipe transport not bundled; install pywin32 shim")
        if not os.path.exists(path):
            raise IpcError(f"Mesen pipe not found: {path} (is the emulator running?)")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(path)
        self._sock = sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._buf = bytearray()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()

    # --- request/response -----------------------------------------------
    def request(self, command: str, **params: Any) -> IpcResponse:
        if self._sock is None:
            self.connect()
        payload = {"command": command, **params}
        line = (json.dumps(payload) + "\n").encode("utf-8")
        assert self._sock is not None
        self._sock.sendall(line)
        raw = self._read_line()
        doc = json.loads(raw)
        return IpcResponse(
            success=bool(doc.get("success")),
            data=doc.get("data"),
            error=doc.get("error"),
        )

    def call(self, command: str, **params: Any) -> dict:
        """Like request, but raises on failure + returns data directly."""
        r = self.request(command, **params)
        if not r.success:
            raise IpcError(f"{command}: {r.error}")
        return r.data or {}

    def _read_line(self) -> str:
        assert self._sock is not None
        while b'\n' not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise IpcError("Pipe closed unexpectedly")
            self._buf.extend(chunk)
        nl = self._buf.index(b'\n')
        line = bytes(self._buf[:nl]).decode('utf-8')
        del self._buf[:nl + 1]
        return line

    # --- convenience wrappers -------------------------------------------
    def read_memory(self, memory_type: str, address: int, length: int) -> bytes:
        data = self.call("readMemory", memoryType=memory_type, address=address, length=length)
        return bytes(data.get("bytes", []))

    def write_memory(self, memory_type: str, address: int, payload: bytes) -> None:
        self.call("writeMemory", memoryType=memory_type, address=address, bytes=list(payload))

    def get_cpu_state(self) -> dict:
        return self.call("getCpuState")

    def set_cpu_state(self, **state) -> None:
        self.call("setCpuState", **state)

    def pause(self) -> None:
        self.call("pause")

    def resume(self) -> None:
        self.call("resume")

    def step(self, count: int = 1) -> None:
        self.call("step", count=count)

    def add_breakpoint(self, address: int, memory_type: str = "SnesPrgRom",
                       break_on: str = "exec") -> int:
        data = self.call("addBreakpoint", address=address, memoryType=memory_type, type=break_on)
        return int(data.get("id", -1))

    def remove_breakpoint(self, bp_id: int) -> None:
        self.call("removeBreakpoint", id=bp_id)

    def evaluate(self, expr: str) -> Any:
        return self.call("evaluate", expression=expr).get("value")

    def take_screenshot(self, path: str) -> None:
        self.call("takeScreenshot", path=path)

    def get_rom_info(self) -> dict:
        return self.call("getRomInfo")

    def get_status(self) -> dict:
        return self.call("getStatus")

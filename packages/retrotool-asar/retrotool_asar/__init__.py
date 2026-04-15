"""Bundled asar (RPGHacker) standalone CLI accessor.

`asar_binary()` returns the path to the vendored executable, or raises
`ToolNotBundledError` if this wheel was built without it. `run_asar` is
a `subprocess.run` convenience wrapper.
"""
from __future__ import annotations

import subprocess
import sys
from importlib.resources import files
from pathlib import Path

__version__ = "0.1.0"

_EXE = ".exe" if sys.platform == "win32" else ""


class ToolNotBundledError(FileNotFoundError):
    """Raised when the bundled asar binary is not present in this wheel."""


def asar_binary() -> Path:
    p = Path(str(files(__package__) / "bin" / f"asar{_EXE}"))
    if not p.exists():
        raise ToolNotBundledError(
            f"Bundled `asar` missing at {p}. This retrotool-asar wheel was "
            f"built without the binary. Reinstall, or build from sdist with "
            f"vendor/asar populated."
        )
    return p


def run_asar(args, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("check", True)
    return subprocess.run([str(asar_binary()), *args], **kw)

"""Bundled xdelta3 binary accessor.

`xdelta3_binary()` returns the path to the vendored executable, or raises
`ToolNotBundledError` if this wheel was built without it. `run_xdelta3`
is a `subprocess.run` convenience wrapper.
"""
from __future__ import annotations

import subprocess
import sys
from importlib.resources import files
from pathlib import Path

__version__ = "0.1.0"

_EXE = ".exe" if sys.platform == "win32" else ""


class ToolNotBundledError(FileNotFoundError):
    """Raised when the bundled xdelta3 binary is not present in this wheel."""


def xdelta3_binary() -> Path:
    p = Path(str(files(__package__) / "bin" / f"xdelta3{_EXE}"))
    if not p.exists():
        raise ToolNotBundledError(
            f"Bundled `xdelta3` missing at {p}. This retrotool-xdelta wheel "
            f"was built without the binary. Reinstall, or build from sdist "
            f"with vendor/xdelta populated."
        )
    return p


def run_xdelta3(args, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("check", True)
    return subprocess.run([str(xdelta3_binary()), *args], **kw)

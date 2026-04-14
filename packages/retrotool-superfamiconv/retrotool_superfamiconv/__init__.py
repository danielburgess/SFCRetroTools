"""Locate bundled SuperFamiconv binary and expose subprocess helpers.

Public API:
    binary_path() -> Path    # absolute path to bundled executable
    run(args, **kw)          # subprocess.run wrapper invoking the binary
"""
from __future__ import annotations

import subprocess
import sys
from importlib.resources import files
from pathlib import Path

__version__ = "0.1.0"

_BIN_NAME = "superfamiconv.exe" if sys.platform == "win32" else "superfamiconv"


def binary_path() -> Path:
    p = Path(str(files(__package__) / "bin" / _BIN_NAME))
    if not p.exists():
        raise FileNotFoundError(
            f"Bundled SuperFamiconv binary missing: {p}. "
            "Wheel may have been built without the binary; reinstall or build from sdist."
        )
    return p


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Invoke bundled superfamiconv with given args. Passes kwargs to subprocess.run."""
    kwargs.setdefault("check", True)
    return subprocess.run([str(binary_path()), *args], **kwargs)

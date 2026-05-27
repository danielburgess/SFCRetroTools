"""Bundled bass v18 (ARM9 fork) standalone CLI accessor.

`bass_binary()` returns the path to the vendored executable, or raises
`ToolNotBundledError` if this wheel was built without it. `run_bass` is
a `subprocess.run` convenience wrapper.

The wheel ships `architectures/` next to the binary so bass's runtime
`Path::program() + "architectures/<name>.arch"` lookup resolves without
extra setup. `architectures_dir()` exposes that path for tooling that
wants to introspect or extend the bundled set.
"""
from __future__ import annotations

import subprocess
import sys
from importlib.resources import files
from pathlib import Path

__version__ = "18.0"

_EXE = ".exe" if sys.platform == "win32" else ""


class ToolNotBundledError(FileNotFoundError):
    """Raised when the bundled bass binary is not present in this wheel."""


def bass_binary() -> Path:
    p = Path(str(files(__package__) / "bin" / f"bass{_EXE}"))
    if not p.exists():
        raise ToolNotBundledError(
            f"Bundled `bass` missing at {p}. This retrotool-bass wheel was "
            f"built without the binary. Reinstall, or build from sdist with "
            f"vendor/bass populated."
        )
    return p


def architectures_dir() -> Path | None:
    """Path to the bundled `architectures/` directory next to the binary,
    or None if the wheel didn't ship one. bass loads `.arch` files from
    here at runtime via its `Path::program()` lookup; consumers usually
    don't need to touch this directly, but it's exposed for tools that
    want to enumerate or copy the bundled architecture set."""
    p = Path(str(files(__package__) / "bin" / "architectures"))
    return p if p.exists() else None


def run_bass(args, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("check", True)
    return subprocess.run([str(bass_binary()), *args], **kw)

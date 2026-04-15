"""project.toml `[build.libsfx]` loader.

Thin facade over `retrotool.asm.libsfx.LibSFXProject.discover` that raises
a clear error when a project.toml exists but has no `[build.libsfx]` table.
"""
from __future__ import annotations

from pathlib import Path

try:
    import tomllib as _toml
except ImportError:  # pragma: no cover
    import tomli as _toml  # type: ignore[no-redef]

from retrotool.asm.libsfx import LibSFXConfig, LibSFXProject


class LibSFXSectionMissing(ValueError):
    """Raised when project.toml lacks a `[build.libsfx]` table."""


def has_libsfx_section(root: Path) -> bool:
    root = Path(root)
    toml = root / "project.toml" if root.is_dir() else root
    if not toml.exists():
        return False
    data = _toml.loads(toml.read_text())
    return "libsfx" in (data.get("build") or {})


def load_libsfx_project(root: Path, *, require_section: bool = False) -> LibSFXProject:
    """Load a libSFX project from `root`.

    If `require_section=True` and no `[build.libsfx]` exists in project.toml,
    raises `LibSFXSectionMissing`.
    """
    root = Path(root).resolve()
    if require_section and not has_libsfx_section(root):
        raise LibSFXSectionMissing(
            f"no [build.libsfx] section found in {root / 'project.toml'}"
        )
    return LibSFXProject.discover(root)


__all__ = ["LibSFXSectionMissing", "has_libsfx_section", "load_libsfx_project", "LibSFXConfig"]

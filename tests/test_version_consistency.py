"""Guard against version drift between `retrotool.__version__` and the
`[project].version` declared in pyproject.toml.

This pair silently diverged before 1.0 (pyproject said 0.9.0 while
`__init__.py` still said 0.8.1). The check compares against the pyproject
*source file* rather than `importlib.metadata.version` so it is correct even
when an editable install's recorded metadata is stale.
"""
import tomllib
from pathlib import Path

import pytest

import retrotool

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


@pytest.mark.skipif(
    not _PYPROJECT.is_file(),
    reason="pyproject.toml not present (running against an installed wheel)",
)
def test_version_matches_pyproject():
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    assert retrotool.__version__ == data["project"]["version"], (
        f"retrotool.__version__ ({retrotool.__version__!r}) != "
        f"pyproject [project].version ({data['project']['version']!r}) — "
        f"bump both together."
    )

"""Tests for the `retrotool libsfx` CLI."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from retrotool.cli import main

_HAS_LIBSFX = False
try:
    import retrotool_libsfx  # noqa: F401
    _HAS_LIBSFX = True
except ImportError:
    pass


def _examples_available() -> bool:
    from retrotool import _toolchain
    try:
        _toolchain.libsfx_examples()
        return True
    except Exception:
        return False


libsfx = pytest.mark.skipif(not _HAS_LIBSFX, reason="retrotool_libsfx not installed")
needs_examples = pytest.mark.skipif(
    not _examples_available(),
    reason="libSFX examples/ not bundled",
)


def test_cli_help_exits_zero():
    r = subprocess.run(
        [sys.executable, "-m", "retrotool", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "libsfx" in r.stdout


def test_cli_libsfx_help():
    r = subprocess.run(
        [sys.executable, "-m", "retrotool", "libsfx", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    for kw in ("scaffold", "build", "info", "clean"):
        assert kw in r.stdout


def test_cli_no_args_errors():
    r = subprocess.run(
        [sys.executable, "-m", "retrotool"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0


@libsfx
@needs_examples
def test_cli_scaffold_and_info(tmp_path, capsys):
    dest = tmp_path / "demo"
    rc = main(["libsfx", "scaffold", str(dest), "--template", "Template"])
    assert rc == 0
    assert (dest / "project.toml").exists()

    rc = main(["libsfx", "info", str(dest)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cpu sources" in out
    assert str(dest) in out


@libsfx
@needs_examples
def test_cli_build_and_clean(tmp_path):
    dest = tmp_path / "demo"
    main(["libsfx", "scaffold", str(dest)])

    rc = main(["libsfx", "build", str(dest)])
    assert rc == 0
    assert (dest / ".build").exists()
    roms = list(dest.glob("*.sfc"))
    assert roms, "expected a .sfc ROM after build"

    rc = main(["libsfx", "clean", str(dest)])
    assert rc == 0
    assert not (dest / ".build").exists()
    assert not list(dest.glob("*.sfc"))


@libsfx
@needs_examples
def test_cli_build_with_debug_flag(tmp_path):
    dest = tmp_path / "demo"
    main(["libsfx", "scaffold", str(dest)])
    rc = main(["libsfx", "build", str(dest), "--debug", "2"])
    assert rc == 0
    syms = list(dest.rglob("*.sym"))
    assert syms, "debug=2 should produce a .sym file"


def test_cli_build_missing_dir_returns_error(tmp_path):
    nope = tmp_path / "not_a_project"
    rc = main(["libsfx", "build", str(nope)])
    assert rc != 0


def test_has_libsfx_section_detection(tmp_path):
    from retrotool.project.libsfx import has_libsfx_section, LibSFXSectionMissing, load_libsfx_project

    # no project.toml
    assert has_libsfx_section(tmp_path) is False

    # with [build] but no libsfx
    (tmp_path / "project.toml").write_text('[build]\nassembler = "asar"\n')
    assert has_libsfx_section(tmp_path) is False
    with pytest.raises(LibSFXSectionMissing):
        load_libsfx_project(tmp_path, require_section=True)

    # with [build.libsfx]
    (tmp_path / "project.toml").write_text('[build.libsfx]\nname = "x"\n')
    assert has_libsfx_section(tmp_path) is True

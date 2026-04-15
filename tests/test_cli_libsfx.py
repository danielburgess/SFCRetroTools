"""Tests for the `retrotool libsfx` CLI."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from retrotool.cli import main

@pytest.fixture
def libsfx_wheel():
    """Skip at call time if the retrotool_libsfx wheel isn't importable."""
    pytest.importorskip("retrotool_libsfx")


@pytest.fixture
def examples_dir(libsfx_wheel):
    """Skip at call time if the bundled libSFX examples/ tree is missing.

    Evaluated after any autouse fixture resets the toolchain cache, so a
    stale collection-time result won't force a skip when the wheel is in fact
    available for this test run.
    """
    from retrotool import _toolchain
    try:
        return _toolchain.libsfx_examples()
    except Exception:
        pytest.skip("libSFX examples/ not bundled")


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


def test_cli_scaffold_and_info(examples_dir, tmp_path, capsys):
    dest = tmp_path / "demo"
    rc = main(["libsfx", "scaffold", str(dest), "--template", "Template"])
    assert rc == 0
    assert (dest / "project.toml").exists()

    rc = main(["libsfx", "info", str(dest)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cpu sources" in out
    assert str(dest) in out


def test_cli_build_and_clean(examples_dir, tmp_path):
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


def test_cli_build_with_debug_flag(examples_dir, tmp_path):
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

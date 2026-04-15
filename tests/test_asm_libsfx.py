"""Tests for retrotool.asm.libsfx — end-to-end scaffold+build of libSFX projects."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from retrotool.asm.libsfx import (
    LibSFXConfig,
    LibSFXProject,
    scaffold_libsfx_project,
)
from retrotool.rom.header import verify_rom

_HAS_LIBSFX = False
try:
    import retrotool_libsfx  # noqa: F401
    _HAS_LIBSFX = True
except ImportError:
    pass

libsfx = pytest.mark.skipif(not _HAS_LIBSFX, reason="retrotool_libsfx not installed")


def _examples_available() -> bool:
    from retrotool import _toolchain
    try:
        _toolchain.libsfx_examples()
        return True
    except Exception:
        return False


needs_examples = pytest.mark.skipif(
    not _examples_available(),
    reason="libSFX examples/ not bundled in this retrotool-libsfx install",
)


@libsfx
@needs_examples
def test_scaffold_template(tmp_path):
    dest = tmp_path / "demo"
    scaffold_libsfx_project(dest, "Template")
    assert (dest / "Template.s").exists()
    assert (dest / "project.toml").exists()


@libsfx
@needs_examples
def test_scaffold_rejects_unknown_template(tmp_path):
    with pytest.raises(FileNotFoundError):
        scaffold_libsfx_project(tmp_path / "x", "DoesNotExist")


@libsfx
@needs_examples
def test_scaffold_rejects_nonempty_dest(tmp_path):
    dest = tmp_path / "demo"
    dest.mkdir()
    (dest / "existing.txt").write_text("hi")
    with pytest.raises(FileExistsError):
        scaffold_libsfx_project(dest, "Template")


@libsfx
@needs_examples
def test_build_template_produces_valid_rom(tmp_path):
    dest = tmp_path / "demo"
    scaffold_libsfx_project(dest, "Template")
    proj = LibSFXProject.discover(dest)
    result = proj.build()

    assert result.rom.exists()
    assert result.rom.stat().st_size >= 0x8000
    assert result.header.is_valid
    assert verify_rom(result.rom)
    assert result.symfile is None
    assert result.breakpoints is None


@libsfx
@needs_examples
def test_build_debug_emits_sym_map_bp(tmp_path):
    dest = tmp_path / "demo"
    scaffold_libsfx_project(dest, "Template")
    proj = LibSFXProject.discover(dest)
    proj.cfg.debug = 2
    result = proj.build()

    assert result.symfile and result.symfile.exists()
    assert result.mapfile and result.mapfile.exists()
    # breakpoints file is emitted even if empty (Template has no ;rwx annotations)
    assert result.breakpoints and result.breakpoints.exists()


@libsfx
@needs_examples
def test_build_is_reproducible(tmp_path):
    dest = tmp_path / "demo"
    scaffold_libsfx_project(dest, "Template")
    proj = LibSFXProject.discover(dest)

    first = proj.build().rom.read_bytes()
    proj.clean()
    second = proj.build().rom.read_bytes()
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


@libsfx
@needs_examples
def test_discover_reads_project_toml(tmp_path):
    dest = tmp_path / "demo"
    scaffold_libsfx_project(dest, "Template")
    (dest / "project.toml").write_text(
        "[build.libsfx]\n"
        'name = "custom"\n'
        "debug = 1\n"
        "stack_size = 0x200\n"
    )
    proj = LibSFXProject.discover(dest)
    assert proj.cfg.name == "custom"
    assert proj.cfg.debug == 1
    assert proj.cfg.stack_size == 0x200


def test_libsfx_config_defaults():
    cfg = LibSFXConfig()
    assert cfg.stack_size == 0x100
    assert cfg.debug == 0
    assert cfg.cpu_sources == []

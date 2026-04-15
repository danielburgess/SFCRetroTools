"""Tests for retrotool._toolchain centralized binary resolution."""
from __future__ import annotations

import subprocess
import sys

import pytest

from retrotool import _toolchain
from retrotool._toolchain import ToolchainError


@pytest.fixture
def libsfx_wheel():
    pytest.importorskip("retrotool_libsfx")


ALL_BINS = [
    "ca65", "ld65", "cc65", "co65", "ar65",
    "superfamiconv", "superfamicheck",
    "brr_encoder", "brr_decoder",
    "lz4", "make_breakpoints",
]


@pytest.mark.parametrize("name", ALL_BINS)
def test_binary_accessor_returns_existing_path(libsfx_wheel, name):
    fn = getattr(_toolchain, name)
    p = fn()
    assert p.exists(), f"{name} resolved to {p} which does not exist"
    assert p.is_file()


@pytest.mark.parametrize("dir_fn", ["libsfx_include", "libsfx_config", "libsfx_packages"])
def test_data_dir_accessor(libsfx_wheel, dir_fn):
    p = getattr(_toolchain, dir_fn)()
    assert p.exists()
    assert p.is_dir()


@pytest.mark.parametrize("name", ["ca65", "ld65", "lz4"])
def test_tool_version_returns_non_empty(libsfx_wheel, name):
    v = _toolchain.tool_version(name)
    assert v, f"no version string from {name}"


def test_unknown_tool_version_raises():
    with pytest.raises(ToolchainError):
        _toolchain.tool_version("not_a_real_tool")


def test_missing_binary_raises_with_install_hint(monkeypatch, tmp_path):
    # Force bundled resolution off + empty $PATH so both lookups fail.
    monkeypatch.setenv("RETROTOOL_USE_SYSTEM_TOOLS", "1")
    monkeypatch.setenv("PATH", str(tmp_path))
    _toolchain.clear_cache()
    with pytest.raises(ToolchainError) as exc:
        _toolchain.ca65()
    assert "retrotool[libsfx]" in str(exc.value)


def test_graphics_wrapper_resolves_through_toolchain(libsfx_wheel):
    """retrotool.graphics.superfamiconv should use _toolchain.superfamiconv()."""
    from retrotool.graphics.superfamiconv import sfc_run
    out = sfc_run(["--version"], capture_output=True, text=True, check=False)
    combined = (out.stdout or "") + (out.stderr or "")
    assert "superfamiconv" in combined.lower() or out.returncode == 0

"""Tests for retrotool._toolchain centralized binary resolution."""
from __future__ import annotations

import subprocess
import sys

import pytest

from retrotool import _toolchain
from retrotool._toolchain import ToolchainError


_HAS_LIBSFX = False
try:
    import retrotool_libsfx  # noqa: F401
    _HAS_LIBSFX = True
except ImportError:
    pass


requires_libsfx = pytest.mark.libsfx
skip_if_no_libsfx = pytest.mark.skipif(not _HAS_LIBSFX, reason="retrotool_libsfx not installed")


ALL_BINS = [
    "ca65", "ld65", "cc65", "co65", "ar65",
    "superfamiconv", "superfamicheck",
    "brr_encoder", "brr_decoder",
    "lz4", "make_breakpoints",
]


@skip_if_no_libsfx
@pytest.mark.parametrize("name", ALL_BINS)
def test_binary_accessor_returns_existing_path(name):
    fn = getattr(_toolchain, name)
    p = fn()
    assert p.exists(), f"{name} resolved to {p} which does not exist"
    assert p.is_file()


@skip_if_no_libsfx
@pytest.mark.parametrize("dir_fn", ["libsfx_include", "libsfx_config", "libsfx_packages"])
def test_data_dir_accessor(dir_fn):
    p = getattr(_toolchain, dir_fn)()
    assert p.exists()
    assert p.is_dir()


@skip_if_no_libsfx
@pytest.mark.parametrize("name", ["ca65", "ld65", "lz4"])
def test_tool_version_returns_non_empty(name):
    v = _toolchain.tool_version(name)
    assert v, f"no version string from {name}"


def test_unknown_tool_version_raises():
    with pytest.raises(ToolchainError):
        _toolchain.tool_version("not_a_real_tool")


def test_missing_binary_raises_with_install_hint(monkeypatch, tmp_path):
    # Force bundled resolution off + empty $PATH so both lookups fail.
    monkeypatch.setattr(_toolchain, "_USE_SYSTEM", True)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ToolchainError) as exc:
        _toolchain.ca65()
    assert "retrotool[libsfx]" in str(exc.value)


@skip_if_no_libsfx
def test_graphics_wrapper_resolves_through_toolchain():
    """retrotool.graphics.superfamiconv should use _toolchain.superfamiconv()."""
    from retrotool.graphics.superfamiconv import sfc_run
    out = sfc_run(["--version"], capture_output=True, text=True, check=False)
    combined = (out.stdout or "") + (out.stderr or "")
    assert "superfamiconv" in combined.lower() or out.returncode == 0

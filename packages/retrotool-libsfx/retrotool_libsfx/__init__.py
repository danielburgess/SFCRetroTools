"""Bundled Optiroc SNES toolchain accessors.

Each `*_binary()` function returns the absolute path to a bundled executable,
or raises `ToolNotBundledError` if the wheel was built without that tool
(e.g. the 0.8.x release only ships SuperFamiconv; the full toolchain lands
in retrotool 0.9.0).

Each `run_*` helper is a `subprocess.run` convenience wrapper.
"""
from __future__ import annotations

import subprocess
import sys
from importlib.resources import files
from pathlib import Path

__version__ = "2026.5.15"

_EXE = ".exe" if sys.platform == "win32" else ""


class ToolNotBundledError(FileNotFoundError):
    """Raised when a bundled tool binary is not present in this wheel."""


def _binary(name: str) -> Path:
    p = Path(str(files(__package__) / "bin" / f"{name}{_EXE}"))
    if not p.exists():
        raise ToolNotBundledError(
            f"Bundled `{name}` missing at {p}. This retrotool-libsfx wheel "
            f"was built without {name}. Reinstall, or build retrotool-libsfx "
            f"from sdist with the corresponding vendor/ submodule populated."
        )
    return p


# Individual binary accessors
def superfamiconv_binary() -> Path:     return _binary("superfamiconv")
def superfamicheck_binary() -> Path:    return _binary("superfamicheck")
def brr_encoder_binary() -> Path:       return _binary("brr_encoder")
def brr_decoder_binary() -> Path:       return _binary("brr_decoder")
def lz4_binary() -> Path:               return _binary("lz4")
def ca65_binary() -> Path:              return _binary("ca65")
def ld65_binary() -> Path:              return _binary("ld65")
def cc65_binary() -> Path:              return _binary("cc65")
def co65_binary() -> Path:              return _binary("co65")
def ar65_binary() -> Path:              return _binary("ar65")
def make_breakpoints_binary() -> Path:  return _binary("make_breakpoints")


# libSFX asm runtime (header tree, not executable)
def libsfx_include_dir() -> Path:
    p = Path(str(files(__package__) / "include"))
    if not p.exists():
        raise ToolNotBundledError(
            f"libSFX include tree missing at {p}. Build from sdist with "
            f"vendor/libSFX populated."
        )
    return p


def libsfx_config_dir() -> Path:
    p = Path(str(files(__package__) / "include" / "Configurations"))
    if not p.exists():
        raise ToolNotBundledError(f"libSFX Configurations/ missing at {p}.")
    return p


def libsfx_packages_dir() -> Path:
    return Path(str(files(__package__) / "include" / "Packages"))


def libsfx_examples_dir() -> Path:
    p = Path(str(files(__package__) / "examples"))
    if not p.exists():
        raise ToolNotBundledError(
            f"libSFX examples tree missing at {p}. Reinstall retrotool-libsfx "
            f"from an sdist/wheel built after examples/ was bundled."
        )
    return p


# subprocess wrappers
def _run(bin_fn, args: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("check", True)
    return subprocess.run([str(bin_fn()), *args], **kwargs)


def run_superfamiconv(args, **kw):    return _run(superfamiconv_binary, args, **kw)
def run_superfamicheck(args, **kw):   return _run(superfamicheck_binary, args, **kw)
def run_brr_encoder(args, **kw):      return _run(brr_encoder_binary, args, **kw)
def run_brr_decoder(args, **kw):      return _run(brr_decoder_binary, args, **kw)
def run_lz4(args, **kw):              return _run(lz4_binary, args, **kw)
def run_ca65(args, **kw):             return _run(ca65_binary, args, **kw)
def run_ld65(args, **kw):             return _run(ld65_binary, args, **kw)
def run_make_breakpoints(args, **kw): return _run(make_breakpoints_binary, args, **kw)


# Back-compat alias for the pre-rename API (retrotool.graphics.superfamiconv
# imported `binary_path` when this package was still `retrotool-superfamiconv`).
def binary_path() -> Path:
    return superfamiconv_binary()


def run(args, **kw):
    return run_superfamiconv(args, **kw)

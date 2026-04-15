"""Centralized binary + data-path resolution for the libSFX toolchain.

All subsystems (graphics, asm, audio, compression, rom, debugger) resolve
bundled or system tools through here. Swap-in point for
`RETROTOOL_USE_SYSTEM_TOOLS=1` env override later.
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path


class ToolchainError(RuntimeError):
    """Raised when a required toolchain binary or data directory is unavailable."""


_INSTALL_HINT = (
    "Install the bundled toolchain with `pip install retrotool[libsfx]`, "
    "or place the binary on $PATH."
)

# System-tool names to search on $PATH when bundled wheel isn't installed.
_PATH_NAMES: dict[str, str] = {
    "ca65": "ca65",
    "ld65": "ld65",
    "cc65": "cc65",
    "co65": "co65",
    "ar65": "ar65",
    "superfamiconv": "superfamiconv",
    "superfamicheck": "superfamicheck",
    "brr_encoder": "brr_encoder",
    "brr_decoder": "brr_decoder",
    "lz4": "lz4",
    "make_breakpoints": "make_breakpoints",
}

def _use_system() -> bool:
    return os.environ.get("RETROTOOL_USE_SYSTEM_TOOLS") == "1"


def _bundled(attr: str) -> Path | None:
    """Try to get a path from retrotool_libsfx. Returns None if wheel missing or tool absent."""
    try:
        import retrotool_libsfx  # type: ignore[import-not-found]
    except ImportError:
        return None
    fn = getattr(retrotool_libsfx, attr, None)
    if fn is None:
        return None
    try:
        return Path(fn())
    except Exception:
        return None


def _resolve_binary(name: str) -> Path:
    """Resolve a binary by name. Bundled wheel first (unless overridden), then $PATH."""
    if not _use_system():
        p = _bundled(f"{name}_binary")
        if p is not None and p.exists():
            return p
    path_hit = shutil.which(_PATH_NAMES[name])
    if path_hit:
        return Path(path_hit)
    raise ToolchainError(f"`{name}` not found (bundled or on $PATH). {_INSTALL_HINT}")


def _resolve_dir(bundled_attr: str, what: str) -> Path:
    p = _bundled(bundled_attr)
    if p is not None and p.exists():
        return p
    raise ToolchainError(f"{what} not available — {_INSTALL_HINT}")


# -------- Binary accessors (cached) --------

@functools.lru_cache(maxsize=None)
def ca65() -> Path:            return _resolve_binary("ca65")
@functools.lru_cache(maxsize=None)
def ld65() -> Path:            return _resolve_binary("ld65")
@functools.lru_cache(maxsize=None)
def cc65() -> Path:            return _resolve_binary("cc65")
@functools.lru_cache(maxsize=None)
def co65() -> Path:            return _resolve_binary("co65")
@functools.lru_cache(maxsize=None)
def ar65() -> Path:            return _resolve_binary("ar65")
@functools.lru_cache(maxsize=None)
def superfamiconv() -> Path:   return _resolve_binary("superfamiconv")
@functools.lru_cache(maxsize=None)
def superfamicheck() -> Path:  return _resolve_binary("superfamicheck")
@functools.lru_cache(maxsize=None)
def brr_encoder() -> Path:     return _resolve_binary("brr_encoder")
@functools.lru_cache(maxsize=None)
def brr_decoder() -> Path:     return _resolve_binary("brr_decoder")
@functools.lru_cache(maxsize=None)
def lz4() -> Path:             return _resolve_binary("lz4")
@functools.lru_cache(maxsize=None)
def make_breakpoints() -> Path:return _resolve_binary("make_breakpoints")


# -------- libSFX data trees --------

@functools.lru_cache(maxsize=None)
def libsfx_include() -> Path:
    return _resolve_dir("libsfx_include_dir", "libSFX include/ tree")


@functools.lru_cache(maxsize=None)
def libsfx_config() -> Path:
    return _resolve_dir("libsfx_config_dir", "libSFX Configurations/ dir")


@functools.lru_cache(maxsize=None)
def libsfx_packages() -> Path:
    return _resolve_dir("libsfx_packages_dir", "libSFX Packages/ dir")


@functools.lru_cache(maxsize=None)
def libsfx_examples() -> Path:
    """libSFX `examples/` tree. Falls back to vendor submodule for dev checkouts
    where retrotool-libsfx is installed but was built before examples were bundled."""
    try:
        return _resolve_dir("libsfx_examples_dir", "libSFX examples/ dir")
    except ToolchainError:
        here = Path(__file__).resolve().parent.parent
        cand = here / "packages" / "retrotool-libsfx" / "vendor" / "libSFX" / "examples"
        if cand.exists():
            return cand
        raise


# -------- Introspection --------

@functools.lru_cache(maxsize=None)
def tool_version(name: str) -> str:
    """Run `<tool> --version` and return first non-empty stdout/stderr line."""
    resolver = {
        "ca65": ca65, "ld65": ld65, "cc65": cc65, "co65": co65, "ar65": ar65,
        "superfamiconv": superfamiconv, "superfamicheck": superfamicheck,
        "brr_encoder": brr_encoder, "brr_decoder": brr_decoder,
        "lz4": lz4, "make_breakpoints": make_breakpoints,
    }.get(name)
    if resolver is None:
        raise ToolchainError(f"unknown tool {name!r}")
    try:
        out = subprocess.run(
            [str(resolver()), "--version"],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired as e:
        raise ToolchainError(f"{name} --version timed out") from e
    blob = (out.stdout or "") + (out.stderr or "")
    for line in blob.splitlines():
        if line.strip():
            return line.strip()
    return ""


def clear_cache() -> None:
    """Reset resolution caches. Useful in tests."""
    for fn in (ca65, ld65, cc65, co65, ar65, superfamiconv, superfamicheck,
               brr_encoder, brr_decoder, lz4, make_breakpoints,
               libsfx_include, libsfx_config, libsfx_packages, libsfx_examples,
               tool_version):
        fn.cache_clear()

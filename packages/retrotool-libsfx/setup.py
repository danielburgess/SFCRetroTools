"""Build the Optiroc SNES toolchain from vendored sources and embed in wheel.

Layout (single submodule, nested):
  vendor/libSFX/                    — pure-asm framework (include tree)
  vendor/libSFX/tools/superfamiconv — tile/palette/tilemap converter
  vendor/libSFX/tools/superfamicheck— SNES header/checksum fixer
  vendor/libSFX/tools/brrtools      — BRR audio encoder/decoder
  vendor/libSFX/tools/lz4           — LZ4 reference compressor
  vendor/libSFX/tools/cc65          — ca65/ld65 assembler + linker
  vendor/libSFX/tools/make_breakpoints — bash script (copied as-is)

We follow libSFX's pinned submodule SHAs for compatibility. Each native tool
is built via its upstream Makefile, binaries collected into
retrotool_libsfx/bin/. The libSFX include/ tree is copied under
retrotool_libsfx/include/ as package data.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.dist import Distribution


class BinaryDistribution(Distribution):
    """Force a platform-specific wheel tag — we ship native binaries."""
    def has_ext_modules(self) -> bool:  # noqa: D401
        return True


HERE = Path(__file__).parent
VENDOR = HERE / "vendor"
LIBSFX = VENDOR / "libSFX"
TOOLS = LIBSFX / "tools"
BIN_DIR = HERE / "retrotool_libsfx" / "bin"
INC_DIR = HERE / "retrotool_libsfx" / "include"
EXAMPLES_DIR = HERE / "retrotool_libsfx" / "examples"

EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""
MAKE_CMD = "mingw32-make" if sys.platform == "win32" else "make"


def _find_binary(root: Path, name: str, extra: list[Path] = ()) -> Path | None:
    """Search common artifact locations produced by ca65/cmake/autotools builds."""
    exe = f"{name}{EXE_SUFFIX}"
    candidates = [
        *(e / exe for e in extra),
        root / "bin" / exe,
        root / "build" / "release" / exe,
        root / "build" / exe,
        root / "src" / exe,
        root / exe,
    ]
    return next((c for c in candidates if c.exists()), None)


def _patch_brrtools(tool_dir: Path) -> None:
    """Fix UB in BRRtools: strncmp reads 8 bytes from a char[4] field, which
    gcc 15 / -O2+ will (correctly) short-circuit to a false-negative. Split
    into two memcmps that respect the declared field sizes."""
    src = tool_dir / "src" / "brr_encoder.c"
    if not src.exists():
        return
    txt = src.read_text()
    bad = 'if(strncmp(hdr.wave_str, "WAVEfmt ", 8))'
    good = 'if(memcmp(hdr.wave_str, "WAVE", 4) || memcmp(hdr.sc1_id, "fmt ", 4))'
    if bad in txt:
        src.write_text(txt.replace(bad, good))
        print(f"[retrotool-libsfx] patched {src.name} (WAVEfmt OOB read)")


def _build_and_collect(tool_dir: Path, binaries: list[str], build_cwd_sub: str = "",
                       make_targets: list[str] = ()) -> list[Path]:
    """Run `make [targets]` in tool_dir[/build_cwd_sub], copy each named binary to BIN_DIR."""
    if not tool_dir.exists():
        print(f"[retrotool-libsfx] skipping {tool_dir.name!r} — not vendored yet")
        return []

    if tool_dir.name == "brrtools":
        _patch_brrtools(tool_dir)

    build_cwd = tool_dir / build_cwd_sub if build_cwd_sub else tool_dir
    subprocess.check_call([MAKE_CMD, "-j", *make_targets], cwd=build_cwd)

    collected = []
    extra_search = [build_cwd] if build_cwd_sub else []
    for name in binaries:
        src = _find_binary(tool_dir, name, extra=extra_search)
        if src is None:
            raise RuntimeError(f"{tool_dir.name}: build did not produce {name}{EXE_SUFFIX}")
        dst = BIN_DIR / f"{name}{EXE_SUFFIX}"
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)
        collected.append(dst)
    return collected


def _copy_script(src: Path, dst_name: str) -> None:
    """Copy a pre-built script (e.g. make_breakpoints bash script) into bin/."""
    if not src.exists():
        print(f"[retrotool-libsfx] skipping script {src.name!r} — not present")
        return
    dst = BIN_DIR / dst_name
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755)


def _copy_libsfx_includes() -> None:
    """Copy libSFX's include/ tree (headers + Configurations/ + Packages/) as package data."""
    src = LIBSFX / "include"
    if not src.exists():
        print("[retrotool-libsfx] skipping libSFX include tree — submodule not vendored yet")
        return
    if INC_DIR.exists():
        shutil.rmtree(INC_DIR)
    shutil.copytree(src, INC_DIR)


def _copy_libsfx_examples() -> None:
    """Copy libSFX's examples/ tree (scaffold templates) as package data."""
    src = LIBSFX / "examples"
    if not src.exists():
        print("[retrotool-libsfx] skipping libSFX examples tree — submodule not vendored yet")
        return
    if EXAMPLES_DIR.exists():
        shutil.rmtree(EXAMPLES_DIR)
    shutil.copytree(src, EXAMPLES_DIR)


def build_toolchain() -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)

    _build_and_collect(TOOLS / "superfamiconv", ["superfamiconv"])
    _build_and_collect(TOOLS / "superfamicheck", ["superfamicheck"])
    _build_and_collect(TOOLS / "brrtools", ["brr_encoder", "brr_decoder"])
    _build_and_collect(TOOLS / "lz4", ["lz4"], build_cwd_sub="programs")
    # cc65 `bin` target builds only the host tools (skips platform runtime libs
    # we don't need — those are for 6502 target ROMs, not SNES 65816 via libSFX).
    _build_and_collect(TOOLS / "cc65", ["ca65", "ld65", "cc65", "co65", "ar65"],
                       make_targets=["bin"])

    # make_breakpoints is a bash script shipped inside libSFX/tools — no build.
    _copy_script(TOOLS / "make_breakpoints", "make_breakpoints")

    _copy_libsfx_includes()
    _copy_libsfx_examples()


def _copy_licenses() -> None:
    """Collect license files for each bundled toolchain component.
    libSFX (MIT), superfamiconv (MIT), superfamicheck (MIT), brrtools (MIT),
    lz4 (BSD-2-Clause + GPLv2 for cli), cc65 (zlib)."""
    lic_dir = HERE / "retrotool_libsfx" / "licenses"
    lic_dir.mkdir(parents=True, exist_ok=True)
    sources = [
        ("libSFX", LIBSFX),
        ("superfamiconv", TOOLS / "superfamiconv"),
        ("superfamicheck", TOOLS / "superfamicheck"),
        ("brrtools", TOOLS / "brrtools"),
        ("lz4", TOOLS / "lz4"),
        ("cc65", TOOLS / "cc65"),
    ]
    for prefix, root in sources:
        if not root.exists():
            continue
        for name in ("LICENSE", "LICENSE.txt", "COPYING", "COPYING.txt", "license.txt", "README.md"):
            src = root / name
            if src.exists():
                shutil.copy2(src, lic_dir / f"{prefix}-{name}")


class BuildPyWithToolchain(build_py):
    def run(self):
        if not os.environ.get("RETROTOOL_SKIP_NATIVE_BUILD"):
            build_toolchain()
            _copy_licenses()
        super().run()


setup(cmdclass={"build_py": BuildPyWithToolchain}, distclass=BinaryDistribution)

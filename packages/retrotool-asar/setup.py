"""Build the asar (RPGHacker) standalone CLI from vendored sources.

Layout:
  vendor/asar/                — upstream repo (MIT)
  vendor/asar/src/asar/       — CMake project, builds `asar` CLI + `libasar`

We build only the standalone CLI (`asar-standalone` / `asar` target). The
shared library isn't shipped here — call sites use subprocess via
retrotool_asar.run_asar(...).
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
    """Force platform-specific wheel tag — we ship a native binary."""
    def has_ext_modules(self) -> bool:
        return True


HERE = Path(__file__).parent
VENDOR = HERE / "vendor"
ASAR_SRC = VENDOR / "asar" / "src" / "asar"
BIN_DIR = HERE / "retrotool_asar" / "bin"

EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""


def _find_asar(build_dir: Path) -> Path | None:
    """asar's CMake project produces the standalone under various names depending
    on generator and platform."""
    candidates = [
        build_dir / "bin" / f"asar{EXE_SUFFIX}",
        build_dir / "bin" / "Release" / f"asar{EXE_SUFFIX}",
        build_dir / f"asar{EXE_SUFFIX}",
        build_dir / "Release" / f"asar{EXE_SUFFIX}",
        build_dir / f"asar-standalone{EXE_SUFFIX}",
    ]
    return next((c for c in candidates if c.exists()), None)


def _build_asar() -> None:
    if not ASAR_SRC.exists():
        print(f"[retrotool-asar] skipping — {ASAR_SRC} not vendored yet")
        return

    build_dir = ASAR_SRC / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    cmake_args = [
        "cmake", "-S", str(ASAR_SRC), "-B", str(build_dir),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DASAR_GEN_EXE=ON",
        "-DASAR_GEN_DLL=OFF",
        "-DASAR_GEN_LIB=OFF",
    ]
    subprocess.check_call(cmake_args, env=env)
    subprocess.check_call(
        ["cmake", "--build", str(build_dir), "--config", "Release", "-j"],
        env=env,
    )

    src = _find_asar(build_dir)
    if src is None:
        raise RuntimeError(
            f"asar build did not produce asar{EXE_SUFFIX}; checked common "
            f"output paths under {build_dir}"
        )
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    dst = BIN_DIR / f"asar{EXE_SUFFIX}"
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755)


def _copy_licenses() -> None:
    """Vendored asar ships as LGPL-3+. Surface the license files inside the
    wheel so downstream consumers satisfy the LGPL attribution requirement."""
    lic_dir = HERE / "retrotool_asar" / "licenses"
    lic_dir.mkdir(parents=True, exist_ok=True)
    asar_root = VENDOR / "asar"
    for name in ("LICENSE", "license-lgpl.txt", "license-gpl.txt", "license-wtfpl.txt", "README.md"):
        src = asar_root / name
        if src.exists():
            shutil.copy2(src, lic_dir / f"asar-{name}")


class BuildPyWithAsar(build_py):
    def run(self):
        # Skip native compile during sdist creation — contributors without
        # cmake/compilers must still be able to produce source distributions.
        if not os.environ.get("RETROTOOL_SKIP_NATIVE_BUILD"):
            _build_asar()
            _copy_licenses()
        super().run()


setup(cmdclass={"build_py": BuildPyWithAsar}, distclass=BinaryDistribution)

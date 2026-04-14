"""Build SuperFamiconv from vendored source and embed binary in wheel.

Vendored source expected at vendor/SuperFamiconv (git submodule).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


HERE = Path(__file__).parent
VENDOR = HERE / "vendor" / "SuperFamiconv"
BIN_DIR = HERE / "retrotool_superfamiconv" / "bin"
BIN_NAME = "superfamiconv.exe" if sys.platform == "win32" else "superfamiconv"


def build_superfamiconv() -> Path:
    if not VENDOR.exists():
        raise RuntimeError(
            f"SuperFamiconv source missing at {VENDOR}. "
            "Run: git submodule update --init --recursive"
        )

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    target = BIN_DIR / BIN_NAME

    make_cmd = "mingw32-make" if sys.platform == "win32" else "make"
    subprocess.check_call([make_cmd, "-j"], cwd=VENDOR)

    exe = "superfamiconv.exe" if sys.platform == "win32" else "superfamiconv"
    candidates = [
        VENDOR / "bin" / exe,
        VENDOR / "build" / "release" / exe,
        VENDOR / "build" / exe,
        VENDOR / exe,
    ]
    src = next((c for c in candidates if c.exists()), None)
    if src is None:
        raise RuntimeError(
            f"Build did not produce {exe}. Searched: {[str(c) for c in candidates]}"
        )
    shutil.copy2(src, target)
    os.chmod(target, 0o755)
    return target


class BuildPyWithBinary(build_py):
    def run(self):
        build_superfamiconv()
        super().run()


setup(cmdclass={"build_py": BuildPyWithBinary})

"""Build xdelta3 from vendored source and embed the binary in the wheel.

Layout:
  vendor/xdelta/xdelta3/   — upstream xdelta repo, xdelta3 subdir (Apache-2.0)

xdelta3 uses autotools. We run `autoreconf -fi && ./configure && make` in the
xdelta3 source dir, then copy the produced `xdelta3` binary into
retrotool_xdelta/bin/ as package data. `--enable-static` keeps the binary
self-contained so the wheel doesn't depend on host xdelta3 runtime libs.
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
XDELTA_SRC = VENDOR / "xdelta" / "xdelta3"
BIN_DIR = HERE / "retrotool_xdelta" / "bin"

EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""
MAKE_CMD = "mingw32-make" if sys.platform == "win32" else "make"


def _patch_xdelta3(src: Path) -> None:
    """xdelta3.h uses C11 `static_assert` but Makefile.am pins `-std=c99` and
    the header doesn't include <assert.h>. Bump to c11 + add the include."""
    mk = src / "Makefile.am"
    if mk.exists():
        txt = mk.read_text()
        new = txt.replace("-std=c99", "-std=c11").replace("-std=c++11", "-std=c++14")
        if new != txt:
            mk.write_text(new)
            print("[retrotool-xdelta] patched Makefile.am: -std=c99→c11, c++11→c++14")

    hdr = src / "xdelta3.h"
    if hdr.exists():
        txt = hdr.read_text()
        marker = "#include <errno.h>\n#include <stdarg.h>"
        patched = "#include <assert.h>\n#include <errno.h>\n#include <stdarg.h>"
        if marker in txt and patched not in txt:
            hdr.write_text(txt.replace(marker, patched))
            print("[retrotool-xdelta] patched xdelta3.h: added <assert.h>")


def _build_xdelta3() -> None:
    if not XDELTA_SRC.exists():
        print(f"[retrotool-xdelta] skipping — {XDELTA_SRC} not vendored yet")
        return

    _patch_xdelta3(XDELTA_SRC)

    env = os.environ.copy()
    # Always regenerate: our Makefile.am patch above invalidates any shipped
    # configure/Makefile.in, and autoreconf is cheap relative to the build.
    subprocess.check_call(["autoreconf", "-fi"], cwd=XDELTA_SRC, env=env)
    subprocess.check_call(
        ["./configure", "--enable-static", "--disable-shared"],
        cwd=XDELTA_SRC, env=env,
    )
    subprocess.check_call([MAKE_CMD, "-j"], cwd=XDELTA_SRC, env=env)

    exe = f"xdelta3{EXE_SUFFIX}"
    candidates = [XDELTA_SRC / exe, XDELTA_SRC / ".libs" / exe]
    src = next((c for c in candidates if c.exists()), None)
    if src is None:
        raise RuntimeError(f"xdelta3 build did not produce {exe}")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    dst = BIN_DIR / exe
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755)


class BuildPyWithXdelta3(build_py):
    def run(self):
        _build_xdelta3()
        super().run()


setup(cmdclass={"build_py": BuildPyWithXdelta3}, distclass=BinaryDistribution)

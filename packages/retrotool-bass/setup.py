"""Build the bass v18 (ARM9 fork) standalone CLI from vendored sources.

Layout:
  vendor/bass/                — upstream repo (ISC); the actually-published
                                module is at vendor/bass/bass/.
  vendor/bass/bass/           — GNUmakefile that produces `out/bass`.
  vendor/bass/nall/           — header-only template library bass depends on.
  vendor/bass/bass/data/architectures/  — runtime-loadable architecture defs.

We invoke `make` in `vendor/bass/bass/`, copy the resulting binary into
`retrotool_bass/bin/bass`, and copy the `data/architectures/` directory
to `retrotool_bass/bin/architectures/` because bass v18 searches
`Path::program() + "architectures/"` (the binary's own dir) at runtime
when looking up architecture tables. Without that copy, `arch snes.cpu`
in a user's patch fails with `unknown architecture`.

The user-data fallback `~/.local/share/bass/architectures/` is also
checked by bass first; ours is the second-tier lookup, which means a
user-customized arch file in that location overrides the bundled one
(intentional — same as system-installed bass).
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
BASS_REPO = VENDOR / "bass"             # outer repo
BASS_SRC = BASS_REPO / "bass"           # the makefile lives here
BASS_ARCH_SRC = BASS_SRC / "data" / "architectures"
BIN_DIR = HERE / "retrotool_bass" / "bin"
ARCH_DST = BIN_DIR / "architectures"

EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""


def _find_bass(build_dir: Path) -> Path | None:
    """bass's GNUmakefile emits `out/bass` (or `out/bass.exe` on win32)."""
    candidates = [
        build_dir / "out" / f"bass{EXE_SUFFIX}",
        build_dir / f"bass{EXE_SUFFIX}",
    ]
    return next((c for c in candidates if c.exists()), None)


def _patch_nall_for_modern_gcc() -> None:
    """Idempotent fix for a header that breaks under modern libstdc++.

    `nall/arithmetic/natural.hpp` uses `std::runtime_error` without
    including `<stdexcept>`. Older libstdc++ (≤ gcc 12) pulled it in
    transitively; gcc 13+ does not. Without the explicit include the
    build fails with `'runtime_error' is not a member of 'std'`.

    The fix is a one-line addition. We do it here (rather than via a
    proper patch file) because the submodule is read-only-ish and the
    fix is mechanical — if it's already present (someone updated nall,
    or an earlier build already patched the file), we're a no-op.
    """
    natural = BASS_REPO / "nall" / "arithmetic" / "natural.hpp"
    if not natural.exists():
        return
    text = natural.read_text(encoding="utf-8", errors="replace")
    if "#include <stdexcept>" in text:
        return
    # Insert after the file's pragma-once / first include block. The
    # canonical structure is `#pragma once\n#include <something>\n`;
    # we tack our line right after `#pragma once`.
    if "#pragma once" in text:
        patched = text.replace(
            "#pragma once",
            "#pragma once\n#include <stdexcept>",
            1,
        )
    else:
        patched = "#include <stdexcept>\n" + text
    natural.write_text(patched, encoding="utf-8")
    print(f"[retrotool-bass] patched {natural} for modern libstdc++")


def _build_bass() -> None:
    if not BASS_SRC.exists() or not (BASS_SRC / "GNUmakefile").exists():
        print(f"[retrotool-bass] skipping — {BASS_SRC} not vendored yet")
        return

    _patch_nall_for_modern_gcc()

    env = os.environ.copy()
    # The GNUmakefile expects an `out/` directory to write into.
    (BASS_SRC / "out").mkdir(exist_ok=True)
    (BASS_SRC / "obj").mkdir(exist_ok=True)

    # `make all` from inside vendor/bass/bass/. nall is at vendor/bass/nall
    # and its path is hardcoded in the GNUmakefile (`../nall`); changing cwd
    # to BASS_SRC makes that relative path resolve correctly.
    cmd = ["make", "-j", "all"]
    subprocess.check_call(cmd, cwd=str(BASS_SRC), env=env)

    src = _find_bass(BASS_SRC)
    if src is None:
        raise RuntimeError(
            f"bass build did not produce bass{EXE_SUFFIX}; checked common "
            f"output paths under {BASS_SRC}"
        )
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    dst = BIN_DIR / f"bass{EXE_SUFFIX}"
    shutil.copy2(src, dst)
    if EXE_SUFFIX == "":
        os.chmod(dst, 0o755)

    # Copy data/architectures/* next to the binary. bass v18 searches
    # `Path::program() + "architectures/<name>.arch"` at runtime; without
    # the bundled files, `arch snes.cpu` (and every other architecture)
    # raises `unknown architecture` even on the happy path.
    if BASS_ARCH_SRC.exists():
        if ARCH_DST.exists():
            shutil.rmtree(ARCH_DST)
        # Copy non-recursively first; some fork variants nest sub-folders
        # that bass doesn't actually load (e.g. `m68k/`). Copy the lot —
        # bass simply ignores irrelevant entries.
        shutil.copytree(BASS_ARCH_SRC, ARCH_DST)


def _copy_licenses() -> None:
    """bass is ISC. Surface the project's license / readme inside the wheel
    so downstream consumers satisfy attribution. We grab whichever of these
    files exist in the vendor tree — the ARM9 fork doesn't always ship a
    standalone LICENSE file, so README.md is the canonical attribution."""
    lic_dir = HERE / "retrotool_bass" / "licenses"
    lic_dir.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE", "LICENSE.txt", "license.txt", "README.md"):
        src = BASS_REPO / name
        if src.exists():
            shutil.copy2(src, lic_dir / f"bass-{name}")


class BuildPyWithBass(build_py):
    def run(self):
        # Skip native compile during sdist creation — contributors without
        # `make` / a C++ compiler must still be able to produce sdists.
        if not os.environ.get("RETROTOOL_SKIP_NATIVE_BUILD"):
            _build_bass()
            _copy_licenses()
        super().run()


setup(cmdclass={"build_py": BuildPyWithBass}, distclass=BinaryDistribution)

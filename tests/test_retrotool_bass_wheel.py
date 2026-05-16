"""Smoke tests for the bundled `retrotool-bass` wheel.

These are skipped unless `retrotool-bass` is importable in the current
environment — install with ``pip install retrotool[bass]`` (or directly
from the local wheel under ``packages/retrotool-bass/dist/``) to exercise
the bundled binary end-to-end.

What's covered:
  * The wheel exposes the documented API surface (`bass_binary`,
    `architectures_dir`, `run_bass`, `ToolNotBundledError`).
  * The shipped binary is a real executable that prints `bass v18`.
  * The bundled `architectures/` directory contains the SNES family of
    `.arch` files that retrotool consumers care about.
  * `apply_bass_patch` against the bundled binary produces the expected
    bytes for a trivial `db` patch — proving end-to-end that the
    patcher resolution path picks up the wheel and bass's
    `Path::program() + "architectures/"` lookup resolves correctly.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# Skip the entire module when the bundled wheel isn't installed. This is
# the same gate the libsfx tests use for `retrotool_libsfx`.
pytest.importorskip("retrotool_bass")


def test_wheel_exposes_documented_api():
    """API surface: bass_binary, architectures_dir, run_bass,
    ToolNotBundledError. Anything else the wheel ships is implementation
    detail — but these four are the contract `apply_bass_patch` and skill
    documentation depend on."""
    import retrotool_bass as rtb
    for name in ("bass_binary", "architectures_dir", "run_bass",
                 "ToolNotBundledError", "__version__"):
        assert hasattr(rtb, name), f"retrotool_bass missing {name!r}"


def test_bass_binary_exists_and_is_executable():
    from retrotool_bass import bass_binary
    p = bass_binary()
    assert p.exists(), f"bass binary not on disk at {p}"
    # POSIX exec bit (Windows skips this — installer doesn't preserve mode).
    if not str(p).endswith(".exe"):
        import os
        assert os.access(p, os.X_OK), f"{p} not executable"


def test_bass_binary_is_v18():
    """bass with no args prints `bass v18\\n...usage:...` to stderr and
    exits non-zero. Good signal that we shipped the right fork (mainline
    bass is v14)."""
    from retrotool_bass import bass_binary
    proc = subprocess.run(
        [str(bass_binary())],
        capture_output=True, text=True,
    )
    # Returncode is non-zero (no source file given) — not what we check.
    assert "bass v18" in (proc.stdout + proc.stderr), (
        f"expected 'bass v18' in usage banner; got:\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )


def test_architectures_dir_includes_snes_set():
    """bass v18 ships a wide architecture catalog. The wheel should bundle
    at least the SNES family (snes.cpu / snes.smp / snes.gsu / wdc65816 /
    spc700) since retrotool's primary use case is SNES patching."""
    from retrotool_bass import architectures_dir
    d = architectures_dir()
    assert d is not None and d.exists(), (
        "architectures dir missing — bass will fail `arch snes.cpu` lookups"
    )
    files = {p.name for p in d.iterdir() if p.is_file()}
    for required in ("snes.cpu.arch", "snes.smp.arch",
                     "wdc65816.arch", "spc700.arch"):
        assert required in files, f"{required} not bundled (have: {sorted(files)})"


def test_apply_bass_patch_uses_bundled_binary(tmp_path):
    """End-to-end via the patcher API. We rely on the resolver's default
    search order: explicit cmd → bundled wheel → PATH. With the wheel
    installed (which is the precondition for this test), the resolver
    must pick up the bundled binary even without PATH cooperation.
    """
    from retrotool.asm.patcher import BassPatch, apply_bass_patch

    rom = tmp_path / "rom.sfc"
    rom.write_bytes(b"\x00" * 0x10000)
    asm = tmp_path / "p.asm"
    asm.write_text(
        "arch snes.cpu\n"
        "output \"out.sfc\", create\n"
        "// `output` is a no-op when bass runs in modify mode (-m); the\n"
        "// patcher passes `-m <out>`, so the working file is the target.\n"
        "org $008000\n"
        "db $42, $43, $44\n"
    )
    out = tmp_path / "out.sfc"
    result = apply_bass_patch(rom, BassPatch(asm_file=asm), out)
    assert result.ok, f"bass failed:\n{result.log}"
    body = out.read_bytes()
    assert body[0:3] == b"\x42\x43\x44", (
        f"expected db $42 $43 $44 at PC 0; got {body[0:3].hex()}"
    )


def test_run_bass_helper_invokes_binary():
    """`run_bass` is the convenience wrapper; verify it forwards args to
    the bundled binary and returns a CompletedProcess."""
    from retrotool_bass import run_bass
    # `bass --help` doesn't exist (any unknown flag triggers the error
    # path), but invoking with `-strict` and no source still hits the
    # binary. Use check=False since bass exits non-zero with no source.
    proc = run_bass([], check=False)
    assert isinstance(proc, subprocess.CompletedProcess)
    assert "bass v18" in (proc.stdout + proc.stderr).decode(
        "utf-8", errors="replace"
    ) if isinstance(proc.stdout, bytes) else (proc.stdout + proc.stderr)

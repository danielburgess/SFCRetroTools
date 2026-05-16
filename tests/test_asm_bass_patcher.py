"""Bass v18 (ARM9 fork) patcher CLI construction.

Mirrors test_asm_patcher (asar). Validates:
  * `-d` / `-c` value reject whitespace and quotes (bass shell tokenizing).
  * Default `bass_cmd="bass"` resolution falls through to PATH and fails
    cleanly with `bass binary not found` when not installed.
  * Generated CLI is `bass -m <out> [-strict] [-d ...] [-c ...] <asm>`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from retrotool.asm.patcher import BassPatch, apply_bass_patch


@pytest.mark.parametrize("bad_value", [
    "My Game",          # space
    "tab\there",        # tab
    "two\nlines",       # newline
    'has"quote',        # double-quote
])
def test_bass_rejects_whitespace_in_define(tmp_path, bad_value):
    rom = tmp_path / "rom.sfc"; rom.write_bytes(b"\x00" * 0x8000)
    asm = tmp_path / "p.asm"; asm.write_text("")
    patch = BassPatch(asm_file=asm, defines={"PATCH_TITLE": bad_value})
    with pytest.raises(ValueError, match="PATCH_TITLE"):
        apply_bass_patch(rom, patch, tmp_path / "out.sfc")


@pytest.mark.parametrize("bad_value", [
    "spaced value",
    'with"quote',
])
def test_bass_rejects_whitespace_in_constant(tmp_path, bad_value):
    rom = tmp_path / "rom.sfc"; rom.write_bytes(b"\x00" * 0x8000)
    asm = tmp_path / "p.asm"; asm.write_text("")
    patch = BassPatch(asm_file=asm, constants={"COUNT": bad_value})
    with pytest.raises(ValueError, match="COUNT"):
        apply_bass_patch(rom, patch, tmp_path / "out.sfc")


def test_bass_missing_binary_returns_descriptive_error(tmp_path, monkeypatch):
    """Resolution stub returns None → wrapper returns ok=False with a log
    pointing at the install routes (bundled wheel / PATH)."""
    import retrotool.asm.patcher as mod
    monkeypatch.setattr(mod, "_resolve_bass", lambda _cmd: None)
    rom = tmp_path / "rom.sfc"; rom.write_bytes(b"\x00" * 0x8000)
    asm = tmp_path / "p.asm"; asm.write_text("")
    patch = BassPatch(asm_file=asm)
    result = apply_bass_patch(rom, patch, tmp_path / "out.sfc")
    assert result.ok is False
    assert "bass binary not found" in result.log


def test_bass_cli_uses_modify_mode(tmp_path, monkeypatch):
    """The generated CLI must use `-m <out>` (modify) — not `-o` (overwrite).
    `-o` would discard the source ROM bytes; the asar-equivalent semantic
    we want is in-place patching of the target file."""
    import retrotool.asm.patcher as mod

    captured: dict[str, list[str]] = {}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, capture_output=True, text=True):
        captured["cmd"] = list(cmd)
        return _Proc()

    # Pretend bass lives at /usr/bin/bass so resolution succeeds.
    monkeypatch.setattr(mod, "_resolve_bass", lambda _cmd: "/usr/bin/bass")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    rom = tmp_path / "rom.sfc"; rom.write_bytes(b"\x00" * 0x8000)
    asm = tmp_path / "p.asm"; asm.write_text("")
    out = tmp_path / "out.sfc"
    patch = BassPatch(
        asm_file=asm,
        defines={"VERSION": "en"},
        constants={"COUNT": "5"},
        strict=True,
    )
    apply_bass_patch(rom, patch, out)

    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/bass"
    assert cmd[1:3] == ["-m", str(out)]
    assert "-strict" in cmd
    # -d and -c follow -strict; values are NAME=VALUE strings.
    assert "-d" in cmd and "VERSION=en" in cmd
    assert "-c" in cmd and "COUNT=5" in cmd
    # Final positional is the entry source.
    assert cmd[-1] == str(asm)


def test_bass_cli_omits_strict_by_default(tmp_path, monkeypatch):
    """`strict=False` (default) should NOT pass `-strict` to bass."""
    import retrotool.asm.patcher as mod

    captured: dict[str, list[str]] = {}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, capture_output=True, text=True):
        captured["cmd"] = list(cmd)
        return _Proc()

    monkeypatch.setattr(mod, "_resolve_bass", lambda _cmd: "/usr/bin/bass")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    rom = tmp_path / "rom.sfc"; rom.write_bytes(b"\x00" * 0x8000)
    asm = tmp_path / "p.asm"; asm.write_text("")
    apply_bass_patch(rom, BassPatch(asm_file=asm), tmp_path / "out.sfc")
    assert "-strict" not in captured["cmd"]


def test_bass_explicit_bass_cmd_overrides_resolver(tmp_path, monkeypatch):
    """Passing `bass_cmd=path` should preempt the bundled-wheel/PATH search.
    `_resolve_bass` short-circuits to `shutil.which(cmd) or cmd` for the
    non-default value, so an absolute path passes through unchanged."""
    import retrotool.asm.patcher as mod

    captured: dict[str, list[str]] = {}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, capture_output=True, text=True):
        captured["cmd"] = list(cmd)
        return _Proc()

    # Pretend the user-supplied path exists by stubbing shutil.which.
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/opt/bass/bass")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    rom = tmp_path / "rom.sfc"; rom.write_bytes(b"\x00" * 0x8000)
    asm = tmp_path / "p.asm"; asm.write_text("")
    apply_bass_patch(
        rom, BassPatch(asm_file=asm),
        tmp_path / "out.sfc",
        bass_cmd="/opt/bass/bass",
    )
    assert captured["cmd"][0] == "/opt/bass/bass"

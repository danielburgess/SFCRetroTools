"""Regression: M2 — asar `-D NAME=VALUE` define values must reject whitespace.

Asar's CLI tokenizer splits on whitespace inside the value, so `KEY=My Game`
becomes two tokens (`-D KEY=My` + a stray `Game` arg). Without validation the
patch silently builds with a wrong define — surface the bad input early."""
from __future__ import annotations

from pathlib import Path

import pytest

from retrotool.asm.patcher import AsarPatch, apply_patch


@pytest.mark.parametrize("bad_value", [
    "My Game",          # space
    "tab\there",        # tab
    "two\nlines",       # newline
    'has"quote',        # double-quote
    "has=equals",       # equals
])
def test_apply_patch_rejects_whitespace_in_define(tmp_path, bad_value):
    rom = tmp_path / "rom.sfc"
    rom.write_bytes(b"\x00" * 0x8000)
    asm = tmp_path / "p.asm"
    asm.write_text("org $008000\n")
    patch = AsarPatch(asm_file=asm, defines={"PATCH_TITLE": bad_value})
    with pytest.raises(ValueError, match="PATCH_TITLE"):
        apply_patch(rom, patch, tmp_path / "out.sfc")


def test_apply_patch_accepts_safe_define_values(tmp_path, monkeypatch):
    """Safe values shouldn't be rejected. We stub asar resolution since no
    binary is required to exercise the validator path."""
    import retrotool.asm.patcher as mod
    monkeypatch.setattr(mod, "_resolve_asar", lambda _cmd: None)
    rom = tmp_path / "rom.sfc"; rom.write_bytes(b"\x00" * 0x8000)
    asm = tmp_path / "p.asm"; asm.write_text("org $008000\n")
    patch = AsarPatch(asm_file=asm, defines={"K": "value-without-spaces"})
    # Resolution stub returns None → patcher returns ok=False (no raise).
    result = apply_patch(rom, patch, tmp_path / "out.sfc")
    assert result.ok is False
    assert "asar binary not found" in result.log

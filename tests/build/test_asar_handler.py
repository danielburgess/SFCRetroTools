"""Phase 5.4 — `<asar>` handler. Asar binary mocked since CI may lack it."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from retrotool.build.handlers import HandlerError, handle_asar
from retrotool.build.spec import Section, SectionKind


def _section(file: str, **attrs):
    return Section(
        kind=SectionKind.ASAR,
        files=[Path(file)],
        attrs={"file": file, **attrs},
        source="test",
    )


def test_asar_missing_file_raises(tmp_path):
    s = _section("nope.asm")
    rom = bytearray(b"\x00" * 0x100)
    with pytest.raises(HandlerError, match="asar patch not found"):
        handle_asar(rom, s, tmp_path)


def test_asar_bad_define_raises(tmp_path, monkeypatch):
    (tmp_path / "p.asm").write_text("")
    s = _section("p.asm", defines="badformat")
    rom = bytearray(b"\x00" * 0x100)
    with pytest.raises(HandlerError, match="missing '='"):
        handle_asar(rom, s, tmp_path)


def test_asar_handler_invokes_patcher(tmp_path, monkeypatch):
    (tmp_path / "p.asm").write_text("org $8000\ndb $42\n")
    s = _section("p.asm", includes=str(tmp_path), defines="K=V|X=1")

    captured = {}

    class _Result:
        ok = True
        log = ""

    def _fake_apply(rom_in, patch, rom_out):
        captured["patch"] = patch
        # Simulate asar writing a one-byte mod at offset 0.
        out = bytearray(rom_in.read_bytes())
        out[0] = 0x42
        rom_out.write_bytes(bytes(out))
        return _Result()

    monkeypatch.setattr("retrotool.asm.patcher.apply_patch", _fake_apply)

    rom = bytearray(b"\x00" * 0x100)
    wr = handle_asar(rom, s, tmp_path)

    assert wr.offset == 0 and wr.length == 0x100
    assert rom[0] == 0x42
    assert captured["patch"].defines == {"K": "V", "X": "1"}
    assert captured["patch"].includes == [tmp_path.resolve()]


@pytest.mark.skipif(shutil.which("asar") is None, reason="asar not installed")
def test_asar_real_smoke(tmp_path):
    """Real asar end-to-end if binary is available locally."""
    (tmp_path / "p.asm").write_text("org $008000\ndb $42, $43\n")
    s = _section("p.asm")
    rom = bytearray(b"\x00" * 0x10000)
    handle_asar(rom, s, tmp_path)
    assert rom[0] == 0x42 and rom[1] == 0x43

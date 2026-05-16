"""<bass> handler — bass v18 (ARM9 fork) assembler.

Mirror of test_asar_handler. Bass binary mocked since CI rarely has it.
The same `_wrap_assembler_writes` post-process drives both handlers, so
behavioral parity (allow-shrink guard, cache opt-in diff, attrs parsing)
is verified for bass directly here.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from retrotool.build.handlers import HandlerError, handle_bass
from retrotool.build.spec import Section, SectionKind


def _section(file: str, **attrs):
    return Section(
        kind=SectionKind.BASS,
        files=[Path(file)],
        attrs={"file": file, **attrs},
        source="test",
    )


def test_bass_missing_file_raises(tmp_path):
    s = _section("nope.asm")
    rom = bytearray(b"\x00" * 0x100)
    with pytest.raises(HandlerError, match="bass patch not found"):
        handle_bass(rom, s, tmp_path)


def test_bass_bad_define_raises(tmp_path):
    (tmp_path / "p.asm").write_text("")
    s = _section("p.asm", defines="badformat")
    rom = bytearray(b"\x00" * 0x100)
    with pytest.raises(HandlerError, match="missing '='"):
        handle_bass(rom, s, tmp_path)


def test_bass_bad_constant_raises(tmp_path):
    (tmp_path / "p.asm").write_text("")
    s = _section("p.asm", constants="alsobad")
    rom = bytearray(b"\x00" * 0x100)
    with pytest.raises(HandlerError, match="missing '='"):
        handle_bass(rom, s, tmp_path)


def test_bass_handler_invokes_patcher(tmp_path, monkeypatch):
    (tmp_path / "p.asm").write_text("arch snes.cpu\norg $008000\ndb $42\n")
    s = _section(
        "p.asm",
        includes=str(tmp_path),
        defines="K=V|X=1",
        constants="N=0x10",
        strict="1",
    )

    captured = {}

    class _Result:
        ok = True
        log = ""

    def _fake_apply(rom_in, patch, rom_out, bass_cmd="bass"):
        captured["patch"] = patch
        captured["bass_cmd"] = bass_cmd
        out = bytearray(rom_in.read_bytes())
        out[0] = 0x42
        rom_out.write_bytes(bytes(out))
        return _Result()

    monkeypatch.setattr("retrotool.asm.patcher.apply_bass_patch", _fake_apply)

    rom = bytearray(b"\x00" * 0x100)
    wr = handle_bass(rom, s, tmp_path)

    assert wr.offset == 0 and wr.length == 0x100
    assert rom[0] == 0x42
    p = captured["patch"]
    assert p.defines == {"K": "V", "X": "1"}
    assert p.constants == {"N": "0x10"}
    assert p.includes == [tmp_path.resolve()]
    assert p.strict is True
    assert captured["bass_cmd"] == "bass"


def test_bass_handler_honors_bass_cmd_attr(tmp_path, monkeypatch):
    (tmp_path / "p.asm").write_text("")
    s = _section("p.asm", **{"bass-cmd": "/opt/bass/bass"})

    captured = {}

    class _Result:
        ok = True
        log = ""

    def _fake_apply(rom_in, patch, rom_out, bass_cmd="bass"):
        captured["bass_cmd"] = bass_cmd
        rom_out.write_bytes(rom_in.read_bytes())
        return _Result()

    monkeypatch.setattr("retrotool.asm.patcher.apply_bass_patch", _fake_apply)

    rom = bytearray(b"\x00" * 0x100)
    handle_bass(rom, s, tmp_path)
    assert captured["bass_cmd"] == "/opt/bass/bass"


def test_bass_handler_surfaces_failure(tmp_path, monkeypatch):
    (tmp_path / "p.asm").write_text("invalid syntax")
    s = _section("p.asm")

    class _Result:
        ok = False
        log = "bass: assembly failed\n"

    def _fake_apply(rom_in, patch, rom_out, bass_cmd="bass"):
        return _Result()

    monkeypatch.setattr("retrotool.asm.patcher.apply_bass_patch", _fake_apply)

    rom = bytearray(b"\x00" * 0x100)
    with pytest.raises(HandlerError, match="bass failed"):
        handle_bass(rom, s, tmp_path)


def test_bass_shrink_rejected(tmp_path, monkeypatch):
    (tmp_path / "p.asm").write_text("")
    s = _section("p.asm")

    class _Result:
        ok = True
        log = ""

    def _fake_apply(rom_in, patch, rom_out, bass_cmd="bass"):
        # Simulate an assembler emitting a shorter file.
        rom_out.write_bytes(b"\x00" * 0x80)
        return _Result()

    monkeypatch.setattr("retrotool.asm.patcher.apply_bass_patch", _fake_apply)
    rom = bytearray(b"\x00" * 0x100)
    with pytest.raises(HandlerError, match="bass shrank ROM"):
        handle_bass(rom, s, tmp_path)


def test_bass_shrink_allowed_when_attr_set(tmp_path, monkeypatch):
    (tmp_path / "p.asm").write_text("")
    s = _section("p.asm", **{"allow-shrink": "1"})

    class _Result:
        ok = True
        log = ""

    def _fake_apply(rom_in, patch, rom_out, bass_cmd="bass"):
        rom_out.write_bytes(b"\x00" * 0x80)
        return _Result()

    monkeypatch.setattr("retrotool.asm.patcher.apply_bass_patch", _fake_apply)
    rom = bytearray(b"\x00" * 0x100)
    wr = handle_bass(rom, s, tmp_path)
    assert wr.offset == 0 and wr.length == 0x80


def test_bass_cache_opt_in_returns_diff_ranges(tmp_path, monkeypatch):
    """cache=True flips handle_bass into diff-mode: return list[WriteRange]
    covering only changed-byte runs, mirroring asar's diff-mode."""
    (tmp_path / "p.asm").write_text("")
    s = _section("p.asm")
    s.cache = True

    class _Result:
        ok = True
        log = ""

    def _fake_apply(rom_in, patch, rom_out, bass_cmd="bass"):
        out = bytearray(rom_in.read_bytes())
        out[0x10] = 0xAA       # one changed byte
        out[0x40:0x44] = b"\xBB\xBB\xBB\xBB"  # one changed run
        rom_out.write_bytes(bytes(out))
        return _Result()

    monkeypatch.setattr("retrotool.asm.patcher.apply_bass_patch", _fake_apply)
    rom = bytearray(b"\x00" * 0x100)
    wr = handle_bass(rom, s, tmp_path)
    # Diff mode returns a list of WriteRange covering only changed runs.
    assert isinstance(wr, list)
    offsets = sorted(w.offset for w in wr)
    lengths = {w.offset: w.length for w in wr}
    assert offsets == [0x10, 0x40]
    assert lengths[0x10] == 1 and lengths[0x40] == 4


@pytest.mark.skipif(shutil.which("bass") is None, reason="bass not installed")
def test_bass_real_smoke(tmp_path):
    """Real bass end-to-end if binary is available locally.

    bass v18 syntax: `arch snes.cpu` (or similar) declares architecture,
    `org` sets the assembly address, `db` writes raw bytes. Assemblage
    against a 64 KiB scratch ROM should land $42 $43 at PC offset 0.
    """
    (tmp_path / "p.asm").write_text(
        "arch snes.cpu\n"
        "org $008000\n"
        "db $42, $43\n"
    )
    s = _section("p.asm")
    rom = bytearray(b"\x00" * 0x10000)
    handle_bass(rom, s, tmp_path)
    assert rom[0] == 0x42 and rom[1] == 0x43

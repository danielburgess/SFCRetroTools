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


def test_asar_cache_opt_in_returns_diff_ranges(tmp_path, monkeypatch):
    """cache=True flips handle_asar into diff-mode: return list[WriteRange]
    covering only changed-byte runs, not the whole ROM."""
    (tmp_path / "p.asm").write_text("org $8000\ndb $AA\n")
    s = _section("p.asm")
    s.cache = True

    class _Result:
        ok = True
        log = ""

    def _fake_apply(rom_in, patch, rom_out):
        out = bytearray(rom_in.read_bytes())
        out[0x10] = 0xAA
        out[0x11] = 0xBB
        out[0x80] = 0xCC
        rom_out.write_bytes(bytes(out))
        return _Result()

    monkeypatch.setattr("retrotool.asm.patcher.apply_patch", _fake_apply)

    rom = bytearray(b"\x00" * 0x100)
    result = handle_asar(rom, s, tmp_path)

    assert isinstance(result, list), "cache=True must emit a write-set list"
    # Two contiguous runs: 0x10..0x12 (2 bytes) and 0x80..0x81 (1 byte).
    assert [(w.offset, w.length) for w in result] == [(0x10, 2), (0x80, 1)]


def test_asar_cache_disabled_returns_whole_rom(tmp_path, monkeypatch):
    """Default (no cache override) keeps the historical whole-ROM WriteRange."""
    (tmp_path / "p.asm").write_text("org $8000\ndb $AA\n")
    s = _section("p.asm")

    class _Result:
        ok = True
        log = ""

    def _fake_apply(rom_in, patch, rom_out):
        out = bytearray(rom_in.read_bytes())
        out[0x10] = 0xAA
        rom_out.write_bytes(bytes(out))
        return _Result()

    monkeypatch.setattr("retrotool.asm.patcher.apply_patch", _fake_apply)

    rom = bytearray(b"\x00" * 0x100)
    result = handle_asar(rom, s, tmp_path)

    from retrotool.build.handlers import WriteRange
    assert isinstance(result, WriteRange)
    assert (result.offset, result.length) == (0, 0x100)


def test_asar_diff_ranges_utility():
    """_diff_ranges coalesces contiguous changed bytes and handles tail growth."""
    from retrotool.build.handlers import _diff_ranges

    before = b"\x00\x00\x00\x00\x00"
    after = b"\x00\xAA\xBB\x00\xCC"
    got = [(w.offset, w.length) for w in _diff_ranges(before, after)]
    assert got == [(1, 2), (4, 1)]

    # Tail extension treated as changed.
    got = [(w.offset, w.length) for w in _diff_ranges(b"\x00" * 4, b"\x00" * 4 + b"\xDE\xAD")]
    assert got == [(4, 2)]

    # No change → empty.
    assert _diff_ranges(b"\x00" * 4, b"\x00" * 4) == []

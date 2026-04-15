"""Tests for retrotool.rom.header — superfamicheck wrapper."""
from __future__ import annotations

from pathlib import Path

import pytest

from retrotool.rom.header import SuperfamicheckError, fix_rom_header, verify_rom


_HAS_LIBSFX = False
try:
    import retrotool_libsfx  # noqa: F401
    _HAS_LIBSFX = True
except ImportError:
    pass


libsfx = pytest.mark.skipif(not _HAS_LIBSFX, reason="retrotool_libsfx not installed")


def _make_rom(path: Path, size_kb: int = 128) -> Path:
    """Synthesize a minimal SNES LoROM with zeroed checksum at $7FC0.

    128KB is the smallest size SuperFamicheck reliably accepts without
    dispute. rom_size byte = log2(size_kb) → $07 for 128KB.
    """
    size = size_kb * 1024
    buf = bytearray(b"\x00" * size)
    hdr_off = 0x7FC0
    buf[hdr_off:hdr_off + 21] = b"RETROTOOL TEST       "
    buf[hdr_off + 0x15] = 0x20                  # LoROM (map_mode)
    buf[hdr_off + 0x16] = 0x00                  # cart type (ROM only)
    buf[hdr_off + 0x17] = 0x07                  # rom size: 2^7 = 128KB
    buf[hdr_off + 0x18] = 0x00                  # sram size
    buf[hdr_off + 0x19] = 0x00                  # country
    buf[hdr_off + 0x1A] = 0x33                  # license ($33 = extended)
    buf[hdr_off + 0x1B] = 0x00                  # version
    # checksum complement + checksum left at $0000 (to be fixed)
    # Put a dummy reset vector pointing into ROM so header parsers are happy.
    buf[0x7FFC] = 0x00
    buf[0x7FFD] = 0x80
    path.write_bytes(bytes(buf))
    return path


@libsfx
def test_fix_rom_header_populates_checksum(tmp_path):
    rom = _make_rom(tmp_path / "stub.sfc")
    result = fix_rom_header(rom)
    assert not result.was_valid
    assert result.is_valid
    assert result.checksum_after != 0
    assert (result.checksum_after ^ result.complement_after) == 0xFFFF


@libsfx
def test_fix_with_out_path_leaves_source_untouched(tmp_path):
    rom = _make_rom(tmp_path / "stub.sfc")
    original = rom.read_bytes()
    out = tmp_path / "fixed.sfc"

    result = fix_rom_header(rom, out=out)

    assert rom.read_bytes() == original
    assert out.exists()
    assert result.path == out
    assert result.is_valid


@libsfx
def test_fix_with_backup_writes_bak(tmp_path):
    rom = _make_rom(tmp_path / "stub.sfc")
    original = rom.read_bytes()
    fix_rom_header(rom, backup=True)
    bak = rom.with_suffix(rom.suffix + ".bak")
    assert bak.exists()
    assert bak.read_bytes() == original
    assert rom.read_bytes() != original


@libsfx
def test_verify_rom_true_after_fix(tmp_path):
    rom = _make_rom(tmp_path / "stub.sfc")
    fix_rom_header(rom)
    assert verify_rom(rom) is True


@libsfx
def test_verify_rom_false_before_fix(tmp_path):
    rom = _make_rom(tmp_path / "stub.sfc")
    assert verify_rom(rom) is False


@libsfx
def test_fix_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        fix_rom_header(tmp_path / "nope.sfc")


@libsfx
def test_verify_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        verify_rom(tmp_path / "nope.sfc")

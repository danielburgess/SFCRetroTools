"""Regression: M1 — `lorom_to_hirom` must mirror bank $00 by default.

Old behavior zeroed (with 0xFF) the HiROM bank-$C0 first 32KB, discarding
LoROM bank $00 contents. HiROM mirroring requires bank $C0 == LoROM $00."""
from __future__ import annotations

from retrotool.core.address import SFCAddressType
from retrotool.core.rom import detect_header, lorom_to_hirom


def _lorom_4_banks() -> bytes:
    """Build a tiny 4-bank LoROM with each bank filled with its bank index."""
    return b"".join(bytes([b]) * 0x8000 for b in range(4))


def test_lorom_to_hirom_mirrors_bank0_by_default():
    src = _lorom_4_banks()
    hi = bytes(lorom_to_hirom(src))
    # HiROM bank $00 (offset 0) should mirror LoROM bank $00 (all 0x00 bytes).
    assert hi[0:0x8000] == b"\x00" * 0x8000
    # And the upper half of HiROM bank 0 mirrors LoROM bank 0 too.
    assert hi[0x8000:0x10000] == b"\x00" * 0x8000


def test_lorom_to_hirom_clear_bank0_opt_in_preserves_legacy_behavior():
    src = _lorom_4_banks()
    hi = bytes(lorom_to_hirom(src, clear_bank0=True))
    # Legacy quirk: bank-$C0 first 32KB filled with 0xFF instead of mirroring.
    assert hi[0:0x8000] == b"\xFF" * 0x8000
    # Upper half still mirrors.
    assert hi[0x8000:0x10000] == b"\x00" * 0x8000


def test_lorom_to_hirom_higher_banks_unaffected_by_flag():
    src = _lorom_4_banks()
    hi_default = bytes(lorom_to_hirom(src))
    hi_legacy = bytes(lorom_to_hirom(src, clear_bank0=True))
    # Bank 1 onward identical regardless of flag.
    assert hi_default[0x10000:] == hi_legacy[0x10000:]
    # Bank 1 page mirrored: both halves equal LoROM bank 1 (bytes of value 1).
    assert hi_default[0x10000:0x18000] == b"\x01" * 0x8000
    assert hi_default[0x18000:0x20000] == b"\x01" * 0x8000


# ---- detect_header (pure-python, no superfamicheck dependency) -----------


def _write_lorom_header(body: bytearray, *, title: bytes = b"RETRO TEST" + b" " * 11,
                       map_mode: int = 0x20, rom_size_code: int = 0x09) -> None:
    off = 0x7FC0
    body[off:off + 21] = title[:21].ljust(21, b" ")
    body[off + 0x15] = map_mode
    body[off + 0x17] = rom_size_code
    body[off + 0x1B] = 0x00
    # Sum body with checksum bytes zeroed, then compute valid complement pair.
    body[off + 0x1C:off + 0x20] = b"\x00\x00\x00\x00"
    csum = sum(body) & 0xFFFF
    comp = csum ^ 0xFFFF
    body[off + 0x1C] = comp & 0xFF
    body[off + 0x1D] = (comp >> 8) & 0xFF
    body[off + 0x1E] = csum & 0xFF
    body[off + 0x1F] = (csum >> 8) & 0xFF
    # Re-sum to fold checksum bytes into themselves (valid SNES convention).
    csum = sum(body) & 0xFFFF
    comp = csum ^ 0xFFFF
    body[off + 0x1C] = comp & 0xFF
    body[off + 0x1D] = (comp >> 8) & 0xFF
    body[off + 0x1E] = csum & 0xFF
    body[off + 0x1F] = (csum >> 8) & 0xFF


def test_detect_header_lorom():
    body = bytearray(0x80_000)
    _write_lorom_header(body)
    h = detect_header(bytes(body))
    assert h is not None
    assert h.address_type == SFCAddressType.LOROM1
    assert h.map_mode == 0x20
    assert h.header_offset == 0x7FC0
    assert h.mapping_name == "lorom"
    assert (h.checksum ^ h.checksum_complement) == 0xFFFF
    assert h.title.startswith("RETRO TEST")


def test_detect_header_ignores_too_small_body():
    # Body shorter than smallest header offset (0x7FC0 + 0x30) returns None.
    assert detect_header(b"\x00" * 0x100) is None


def test_detect_header_prefers_lorom_over_hirom_when_both_parse():
    # Plant a valid-complement LoROM header; HiROM offset is also valid length,
    # but its map_mode/checksum score lower, so LoROM wins.
    body = bytearray(0x200_000)  # large enough for HiROM too
    _write_lorom_header(body)
    h = detect_header(bytes(body))
    assert h is not None
    assert h.address_type == SFCAddressType.LOROM1

"""Regression: H1 — `lorom1_to_pc` guard window.

Old guard `not (0x8000 <= x <= 0x6FFFFF)` was an empty interval; every
out-of-range address fell through to the bit-twiddle and produced garbage."""
from __future__ import annotations

import pytest

from retrotool.core.address import SFCAddress


@pytest.mark.parametrize("snes_addr,expected_pc", [
    (0x008000, 0x000000),       # bank $00 page-start → PC 0
    (0x00FFFF, 0x007FFF),       # bank $00 last page byte → PC 0x7FFF
    (0x018000, 0x008000),       # bank $01 page-start → PC 0x8000
    (0x6FFFFF, 0x37FFFF),       # last in-range LoROM1 byte
])
def test_lorom1_to_pc_in_range(snes_addr, expected_pc):
    assert SFCAddress.lorom1_to_pc(snes_addr, verbose=False) == expected_pc


@pytest.mark.parametrize("snes_addr", [
    0x000000,    # below bank window (page<$8000)
    0x007FFF,    # bank $00 page-low → invalid page
    0x018000 - 1,  # bank $01 page-low boundary
    0x010000,    # bank $01 page <$8000
    0x700000,    # bank $70 — above LoROM1 bank window
    0x7FFFFF,    # well above
    0x800000,    # LoROM2 territory
])
def test_lorom1_to_pc_out_of_range(snes_addr):
    """Without `fallback=True` the call must return None for any address that
    isn't in the LoROM1 window (banks $00–$6F, pages $8000–$FFFF)."""
    assert SFCAddress.lorom1_to_pc(snes_addr, verbose=False, fallback=False) is None


def test_lorom1_to_pc_fallback_to_lorom2_when_out_of_range():
    """If fallback=True, an out-of-range LoROM1 addr should try lorom2_to_pc."""
    # 0x808000 is the start of LoROM2 — out of LoROM1 window, in LoROM2 window.
    assert SFCAddress.lorom1_to_pc(0x808000, verbose=False, fallback=True) == 0x000000

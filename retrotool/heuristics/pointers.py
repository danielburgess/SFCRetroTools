"""Pointer table scanner. Locate candidate pointer tables in a ROM."""
from __future__ import annotations

from dataclasses import dataclass

from retrotool.core.address import SFCAddress, SFCAddressType
from retrotool.core.binary import read_u16_le, read_u24_le


@dataclass
class PointerTableCandidate:
    offset: int              # PC offset of table start
    entry_size: int          # 2 or 3
    count: int               # number of entries
    target_low: int          # PC offset range of targets (min)
    target_high: int
    monotonic_fraction: float


def scan_pointer_tables(
    rom: bytes,
    entry_size: int = 2,
    bank: int | None = None,
    address_type: int = SFCAddressType.LOROM1,
    min_entries: int = 8,
    max_entries: int = 512,
    valid_range: tuple[int, int] | None = None,
    step: int = 2,
) -> list[PointerTableCandidate]:
    """Linear scan for runs of pointers that all resolve into `valid_range`.

    For 2-byte pointers, `bank` supplies the bank byte. If None, uses each
    pointer's own implicit bank (typically the bank where the table lives)."""
    if entry_size not in (2, 3):
        raise ValueError("entry_size must be 2 or 3")
    low, high = valid_range if valid_range else (0, len(rom))
    out: list[PointerTableCandidate] = []
    i = 0
    while i <= len(rom) - entry_size * min_entries:
        count = 0
        last_target = -1
        monotonic = 0
        j = i
        tmin = len(rom)
        tmax = 0
        while count < max_entries and j + entry_size <= len(rom):
            if entry_size == 2:
                rel = read_u16_le(rom, j)
                snes = ((bank if bank is not None else (j >> 16)) << 16) | rel
            else:
                snes = read_u24_le(rom, j)
            pc = SFCAddress(snes, address_type).get_address(SFCAddressType.PC)
            if pc is None or not (low <= pc < high):
                break
            if pc > last_target:
                monotonic += 1
            last_target = pc
            tmin = min(tmin, pc)
            tmax = max(tmax, pc)
            count += 1
            j += entry_size
        if count >= min_entries:
            out.append(PointerTableCandidate(
                offset=i, entry_size=entry_size, count=count,
                target_low=tmin, target_high=tmax,
                monotonic_fraction=monotonic / count,
            ))
            i = j
        else:
            i += step
    return out

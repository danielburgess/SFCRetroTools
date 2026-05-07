"""ROM header detection, mapping-type inference, layout helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from retrotool.core.address import SFCAddressType

SMC_HEADER_SIZE = 512

# Internal header candidate offsets (PC) per mapping mode
_HEADER_OFFSETS = {
    SFCAddressType.LOROM1: 0x7FC0,
    SFCAddressType.HIROM: 0xFFC0,
    SFCAddressType.EXHIROM: 0x40FFC0,
}

_MAP_MODE_NAMES = {
    0x20: "lorom",
    0x21: "hirom",
    0x22: "lorom-sa1",
    0x23: "sa1",
    0x25: "exhirom",
    0x30: "lorom-fastrom",
    0x31: "hirom-fastrom",
    0x35: "exhirom-fastrom",
}


@dataclass
class RomHeader:
    title: str
    map_mode: int
    cartridge_type: int
    rom_size_code: int
    ram_size_code: int
    country: int
    developer: int
    version: int
    checksum_complement: int
    checksum: int
    header_offset: int           # PC offset where internal header starts
    address_type: int            # SFCAddressType inferred from header offset

    @property
    def mapping_name(self) -> str:
        return _MAP_MODE_NAMES.get(self.map_mode, f"unknown({self.map_mode:#04X})")

    @property
    def rom_size_bytes(self) -> int:
        return 1 << self.rom_size_code if self.rom_size_code else 0


def _strip_smc_header(raw: bytes) -> tuple[Optional[bytes], bytes]:
    """Return (smc_header_or_None, body). SMC header = 512 bytes if ROM size % 0x400 == 0x200."""
    if len(raw) % 0x400 == SMC_HEADER_SIZE:
        return raw[:SMC_HEADER_SIZE], raw[SMC_HEADER_SIZE:]
    return None, raw


def detect_header(body: bytes) -> Optional[RomHeader]:
    """Try known header offsets, score map_mode byte, return best match."""
    best: Optional[RomHeader] = None
    best_score = -1
    for addr_type, offset in _HEADER_OFFSETS.items():
        if offset + 0x30 > len(body):
            continue
        parsed = _parse_header(body, offset, addr_type)
        score = _score_header(parsed, addr_type)
        if score > best_score:
            best_score = score
            best = parsed
    return best if best_score > 0 else None


def _parse_header(body: bytes, offset: int, addr_type: int) -> RomHeader:
    title = body[offset:offset + 21].rstrip(b' \x00').decode('ascii', errors='replace')
    return RomHeader(
        title=title,
        map_mode=body[offset + 0x15],
        cartridge_type=body[offset + 0x16],
        rom_size_code=body[offset + 0x17],
        ram_size_code=body[offset + 0x18],
        country=body[offset + 0x19],
        developer=body[offset + 0x1A],
        version=body[offset + 0x1B],
        checksum_complement=body[offset + 0x1C] | (body[offset + 0x1D] << 8),
        checksum=body[offset + 0x1E] | (body[offset + 0x1F] << 8),
        header_offset=offset,
        address_type=addr_type,
    )


def _score_header(h: RomHeader, addr_type: int) -> int:
    """Higher = more likely correct. Checksum complement XOR is the strongest signal."""
    score = 0
    if (h.checksum ^ h.checksum_complement) == 0xFFFF and h.checksum != 0:
        score += 10
    expected_modes = {
        SFCAddressType.LOROM1: {0x20, 0x22, 0x23, 0x30},
        SFCAddressType.HIROM: {0x21, 0x31},
        SFCAddressType.EXHIROM: {0x25, 0x35},
    }
    if h.map_mode in expected_modes.get(addr_type, set()):
        score += 5
    if all(0x20 <= b < 0x80 or b == 0 for b in h.title.encode('ascii', errors='replace')):
        score += 1
    return score


def lorom_to_hirom(in_data, *, clear_bank0: bool = False) -> list:
    """Double every bank: LoROM image → HiROM layout.

    `clear_bank0=True` preserves the legacy quirk that filled HiROM bank `$C0`
    with `0xFF` instead of mirroring LoROM bank `$00`. Default off — bank 0 is
    real ROM data and must be mirrored for a valid HiROM image."""
    final_data = [0xFF] * (len(in_data) * 2)
    div = 0x8000
    pcs = len(in_data) // div
    for c in range(pcs):
        for d in range(div):
            pc_pos = d + (c * div)
            hirom_pos = d + (c * 0x10000)
            final_data[hirom_pos] = 0xFF if (clear_bank0 and c == 0) else in_data[pc_pos]
            final_data[hirom_pos + div] = in_data[pc_pos]
    return final_data

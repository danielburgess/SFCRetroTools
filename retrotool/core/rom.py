"""ROM loader, header detection, mapping-type inference."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from retrotool.core.address import SFCAddress, SFCAddressType

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


class Rom:
    """Loaded ROM. Auto-detects SMC header + mapping mode."""

    def __init__(self, data: bytes, path: Optional[Path] = None,
                 smc_header: Optional[bytes] = None,
                 header: Optional[RomHeader] = None):
        self.data = data
        self.path = path
        self.smc_header = smc_header
        self.header = header

    @classmethod
    def load(cls, path: Union[str, Path]) -> "Rom":
        path = Path(path)
        raw = path.read_bytes()
        smc_header, body = _strip_smc_header(raw)
        header = detect_header(body)
        return cls(data=body, path=path, smc_header=smc_header, header=header)

    def __len__(self) -> int:
        return len(self.data)

    def read(self, pc_offset: int, length: int) -> bytes:
        return self.data[pc_offset:pc_offset + length]

    def read_snes(self, snes_addr: int, length: int) -> bytes:
        """Read by SNES address using ROM's mapping mode."""
        if self.header is None:
            raise ValueError("Cannot resolve SNES address: no header detected")
        pc = SFCAddress(snes_addr, self.header.address_type).get_address(SFCAddressType.PC)
        if pc is None:
            raise ValueError(f"Invalid SNES address for {self.header.mapping_name}: {snes_addr:#08X}")
        return self.read(pc, length)


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


def lorom_to_hirom(in_data) -> list:
    """Double every bank: LoROM image → HiROM layout. Quick & dirty."""
    final_data = [0xFF] * (len(in_data) * 2)
    div = 0x8000
    pcs = len(in_data) // div
    for c in range(pcs):
        for d in range(div):
            pc_pos = d + (c * div)
            hirom_pos = d + (c * 0x10000)
            final_data[hirom_pos] = 0xFF if c == 0 else in_data[pc_pos]
            final_data[hirom_pos + div] = in_data[pc_pos]
    return final_data

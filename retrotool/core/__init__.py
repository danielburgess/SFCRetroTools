"""retrotool.core — platform-agnostic primitives (ROM, addressing, binary, cache)."""
from retrotool.core.address import SFCAddress, SFCAddressType
from retrotool.core.binary import (
    bank_byte,
    hex_fmt,
    high_byte,
    integer_or_hex,
    low_byte,
    read_u8,
    read_u16_le,
    read_u24_le,
    write_u16_le,
    write_u24_le,
)
from retrotool.core.cache import BuildCache, CacheEntry, sha256_bytes, sha256_file, sha256_many
from retrotool.core.pointer import SFCPointer
from retrotool.core.rom import RomHeader, detect_header, lorom_to_hirom

__all__ = [
    "SFCAddress",
    "SFCAddressType",
    "SFCPointer",
    "RomHeader",
    "detect_header",
    "lorom_to_hirom",
    "BuildCache",
    "CacheEntry",
    "sha256_bytes",
    "sha256_file",
    "sha256_many",
    "integer_or_hex",
    "hex_fmt",
    "low_byte",
    "high_byte",
    "bank_byte",
    "read_u8",
    "read_u16_le",
    "read_u24_le",
    "write_u16_le",
    "write_u24_le",
]

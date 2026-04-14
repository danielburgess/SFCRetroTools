"""Binary/byte helpers. Shared primitives used across core modules."""
from __future__ import annotations

from functools import lru_cache
from typing import Union


def integer_or_hex(value: Union[int, str], mask: int = 0xFF) -> int:
    """Normalize int or hex string to masked int. Raises ValueError on bad input."""
    if isinstance(value, str):
        if value.upper().startswith('0X'):
            value = value.replace('0X', '').replace('0x', '')
        try:
            value = int(value, 16)
        except ValueError:
            raise ValueError('`address` parameter must be an integer or a hexadecimal string!')
    elif not isinstance(value, int):
        raise ValueError('`address` parameter must be an integer or a hexadecimal string!')
    return value & mask


def hex_fmt(value: int, pad: int = 4, prefix: str = '0x') -> str:
    return f'{prefix}{value:0{pad}X}'


@lru_cache(0xFFFFFF)
def low_byte(addr: int) -> int:
    return addr & 0xFF


@lru_cache(0xFFFFFF)
def high_byte(addr: int) -> int:
    return (addr >> 8) & 0xFF


@lru_cache(0xFFFFFF)
def bank_byte(addr: int) -> int:
    return (addr >> 16) & 0xFF


def read_u8(data: bytes, offset: int) -> int:
    return data[offset]


def read_u16_le(data: bytes, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)


def read_u24_le(data: bytes, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16)


def write_u16_le(value: int) -> bytes:
    return bytes([value & 0xFF, (value >> 8) & 0xFF])


def write_u24_le(value: int) -> bytes:
    return bytes([value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF])

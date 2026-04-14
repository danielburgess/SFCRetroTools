"""SNES BGR555 palette handling. Convert to/from RGB888."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


def bgr555_to_rgb888(word: int) -> tuple[int, int, int]:
    """SNES palette entry (16-bit -bbbbbgggggrrrrr) → RGB 8-bit triple."""
    r = (word >> 0) & 0x1F
    g = (word >> 5) & 0x1F
    b = (word >> 10) & 0x1F
    return (r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2)


def rgb888_to_bgr555(r: int, g: int, b: int) -> int:
    return ((b >> 3) & 0x1F) << 10 | ((g >> 3) & 0x1F) << 5 | ((r >> 3) & 0x1F)


def decode_palette(data: bytes, offset: int = 0, count: int = 16) -> list[tuple[int, int, int]]:
    """Read `count` BGR555 entries → list of RGB888 tuples."""
    out: list[tuple[int, int, int]] = []
    for i in range(count):
        word = data[offset + i * 2] | (data[offset + i * 2 + 1] << 8)
        out.append(bgr555_to_rgb888(word))
    return out


def encode_palette(rgb: Sequence[tuple[int, int, int]]) -> bytes:
    out = bytearray()
    for r, g, b in rgb:
        w = rgb888_to_bgr555(r, g, b)
        out.append(w & 0xFF)
        out.append((w >> 8) & 0xFF)
    return bytes(out)


@dataclass
class Palette:
    """N-color palette; color 0 is conventionally transparent for sprites."""
    colors: list[tuple[int, int, int]]
    transparent_index: int = 0

    @classmethod
    def from_bytes(cls, data: bytes, offset: int = 0, count: int = 16,
                   transparent_index: int = 0) -> "Palette":
        return cls(colors=decode_palette(data, offset, count), transparent_index=transparent_index)

    def to_bytes(self) -> bytes:
        return encode_palette(self.colors)

    def rgba(self, index: int) -> tuple[int, int, int, int]:
        r, g, b = self.colors[index]
        a = 0 if index == self.transparent_index else 255
        return r, g, b, a

    def __len__(self) -> int:
        return len(self.colors)

    def __getitem__(self, i: int) -> tuple[int, int, int]:
        return self.colors[i]

    def __iter__(self) -> Iterable[tuple[int, int, int]]:
        return iter(self.colors)

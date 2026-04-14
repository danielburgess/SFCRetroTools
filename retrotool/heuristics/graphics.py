"""Graphics data signature detection.

Heuristic: graphics blocks have high byte-value entropy but show structure
when reshaped as 8x8 tiles (adjacent bytes often correlate in bitplane pairs).
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class GraphicsCandidate:
    offset: int
    length: int
    bpp: int
    entropy: float
    plane_correlation: float


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in counts if c)


def _plane_pair_correlation(data: bytes) -> float:
    """Mean abs difference between adjacent bytes, scaled to [0,1].

    Bitplane pairs in SNES tiles tend to share structure; low diff = likely gfx."""
    if len(data) < 2:
        return 0.0
    total = sum(abs(data[i] - data[i - 1]) for i in range(1, len(data)))
    return 1.0 - (total / (len(data) - 1) / 255.0)


def scan_graphics(
    rom: bytes,
    bpp: int = 4,
    tile_bytes: int = 32,
    window_tiles: int = 32,
    step_tiles: int = 4,
    min_entropy: float = 4.0,
    min_correlation: float = 0.5,
) -> list[GraphicsCandidate]:
    """Slide a `window_tiles` sized window across ROM; score each."""
    window_bytes = tile_bytes * window_tiles
    step_bytes = tile_bytes * step_tiles
    out: list[GraphicsCandidate] = []
    for off in range(0, len(rom) - window_bytes, step_bytes):
        block = rom[off:off + window_bytes]
        ent = shannon_entropy(block)
        corr = _plane_pair_correlation(block)
        if ent >= min_entropy and corr >= min_correlation:
            out.append(GraphicsCandidate(
                offset=off, length=window_bytes, bpp=bpp,
                entropy=ent, plane_correlation=corr,
            ))
    return out

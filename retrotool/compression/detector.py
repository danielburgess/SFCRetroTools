"""Heuristic compression-block detection.

Scan ROM for plausible LZSS block starts by trying trial decompression against
each registered preset. A candidate is reported when decompression yields a
reasonable size and expands more than it consumes.
"""
from __future__ import annotations

from dataclasses import dataclass

from retrotool.compression.lzss import LZSSCodec, LZSSParams


@dataclass
class CompressionCandidate:
    offset: int
    scheme: str
    consumed: int
    decompressed_size: int
    ratio: float          # decompressed / consumed


def scan_lzss(
    data: bytes,
    presets: list[tuple[str, LZSSParams]],
    start: int = 0,
    end: int | None = None,
    step: int = 1,
    min_ratio: float = 1.2,
    min_size: int = 32,
    max_size: int = 0x10000,
) -> list[CompressionCandidate]:
    """Brute-force scan for LZSS blocks. Expensive — narrow `start/end/step`."""
    if end is None:
        end = len(data) - 4
    results: list[CompressionCandidate] = []
    for scheme_name, params in presets:
        codec = LZSSCodec(params)
        off = start
        while off < end:
            try:
                block = codec.decompress(data, off)
            except (IndexError, ValueError):
                off += step
                continue
            dsize = len(block.data)
            if min_size <= dsize <= max_size and block.consumed > 2:
                ratio = dsize / block.consumed
                if ratio >= min_ratio:
                    results.append(CompressionCandidate(
                        offset=off, scheme=scheme_name,
                        consumed=block.consumed, decompressed_size=dsize, ratio=ratio,
                    ))
            off += step
    return results

"""Simple RLE variants. The canonical 'ctrl-byte' form used on several SNES games:

  ctrl & 0x80 = run flag, ctrl & 0x7F + 1 = length
  run: one byte follows, repeat it `length` times
  literal: `length` bytes follow, copy verbatim
  ctrl == 0x00 end marker (optional per variant)
"""
from __future__ import annotations

from dataclasses import dataclass

from retrotool.compression.base import Codec, CompressResult, DecompressResult


@dataclass(frozen=True)
class RLEParams:
    run_flag: int = 0x80              # bit in ctrl byte that marks a run vs literal
    length_mask: int = 0x7F           # remaining bits encode length-1
    end_marker: int = -1              # -1 disables; 0 = stop on ctrl == 0


class RLECodec(Codec):
    name = "rle"

    def __init__(self, params: RLEParams = RLEParams(), size: int = -1):
        """size: known decompressed size, or -1 to rely on end_marker or end of input."""
        self.params = params
        self.size = size

    def decompress(self, data: bytes, offset: int = 0) -> DecompressResult:
        p = self.params
        out = bytearray()
        src = offset
        n = len(data)
        while src < n:
            ctrl = data[src]; src += 1
            if ctrl == p.end_marker:
                break
            length = (ctrl & p.length_mask) + 1
            if ctrl & p.run_flag:
                b = data[src]; src += 1
                out.extend([b] * length)
            else:
                out.extend(data[src:src + length])
                src += length
            if self.size >= 0 and len(out) >= self.size:
                out = out[:self.size]
                break
        return DecompressResult(data=bytes(out), consumed=src - offset)

    def compress(self, data: bytes) -> CompressResult:
        p = self.params
        out = bytearray()
        n = len(data)
        i = 0
        max_len = p.length_mask + 1
        while i < n:
            # Count run
            run = 1
            while i + run < n and run < max_len and data[i + run] == data[i]:
                run += 1
            if run >= 3:
                out.append(p.run_flag | (run - 1))
                out.append(data[i])
                i += run
                continue
            # Literal scan — continue while next run would be <3
            start = i
            while i < n and (i + 1 >= n or i - start + 1 >= max_len or data[i + 1] != data[i]
                             or (i + 2 < n and data[i + 2] != data[i])):
                i += 1
                if i - start >= max_len:
                    break
            length = i - start
            out.append(length - 1)
            out.extend(data[start:start + length])
        if p.end_marker >= 0:
            out.append(p.end_marker)
        return CompressResult(data=bytes(out), original_size=n)

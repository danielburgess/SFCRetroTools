"""Parameterized LZSS codec. Covers ZAMN, RBShura, legacy variants.

All three share: 4096-byte ring buffer, init_pos=0xFEE, 12-bit offset + 4-bit
length back-references, LSB-first control bits (1=literal, 0=reference).
They differ in: header format (2B LE or none, optional chain bit),
ring-buffer fill byte, and chaining.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from retrotool.compression.base import Codec, CompressResult, DecompressResult


@dataclass(frozen=True)
class LZSSParams:
    window_size: int = 0x1000         # ring buffer bytes
    init_pos: int = 0xFEE             # initial write head
    fill_byte: int = 0x00             # ring buffer prefill
    min_match: int = 3
    max_match: int = 18               # encoded as (len - min_match) in 4 bits
    header: str = "u16_le"            # 'u16_le' | 'u16_le_chain15' | 'none'
    chained: bool = False             # only meaningful with header='u16_le_chain15'


# Pre-canned param sets matching the three known variants
PARAMS_RBSHURA = LZSSParams(fill_byte=0x00, header="u16_le", chained=False)
PARAMS_ZAMN = LZSSParams(fill_byte=0x20, header="u16_le_chain15", chained=True)
PARAMS_LEGACY = LZSSParams(fill_byte=0x00, header="none", chained=False)


def _read_header(data: bytes, offset: int, params: LZSSParams) -> tuple[int, bool, int]:
    """Return (size, chain_flag, header_bytes)."""
    if params.header == "none":
        return len(data) - offset, False, 0
    if params.header == "u16_le":
        word = data[offset] | (data[offset + 1] << 8)
        return word, False, 2
    if params.header == "u16_le_chain15":
        word = data[offset] | (data[offset + 1] << 8)
        chain = bool(word & 0x8000)
        return word & 0x7FFF, chain, 2
    raise ValueError(f"Unknown header format: {params.header!r}")


class LZSSCodec(Codec):
    name = "lzss"

    def __init__(self, params: LZSSParams = PARAMS_RBSHURA):
        self.params = params

    def decompress(self, data: bytes, offset: int = 0) -> DecompressResult:
        """Decompress a single block. For chained ZAMN data, use decompress_chain."""
        p = self.params
        size, _chain, header_bytes = _read_header(data, offset, p)

        ring = bytearray([p.fill_byte] * p.window_size)
        wpos = p.init_pos
        win_mask = p.window_size - 1

        out = bytearray()
        src = offset + header_bytes
        end = src + size
        ctrl = 0
        ctrl_bits = 0

        while src < end:
            if ctrl_bits == 0:
                ctrl = data[src]
                src += 1
                ctrl_bits = 8
                if src >= end:
                    break
            if ctrl & 0x01:
                # literal
                b = data[src]; src += 1
                out.append(b)
                ring[wpos] = b
                wpos = (wpos + 1) & win_mask
            else:
                # back-reference: [off_lo8] [((off>>8)<<4)|(len-min_match)]
                lo = data[src]; src += 1
                hi = data[src]; src += 1
                rpos = (lo | ((hi & 0xF0) << 4)) & win_mask
                length = (hi & 0x0F) + p.min_match
                for _ in range(length):
                    b = ring[rpos]
                    rpos = (rpos + 1) & win_mask
                    out.append(b)
                    ring[wpos] = b
                    wpos = (wpos + 1) & win_mask
            ctrl >>= 1
            ctrl_bits -= 1

        return DecompressResult(data=bytes(out), consumed=src - offset)

    def decompress_chain(
        self,
        data: bytes,
        offset: int,
        resolve_next: Callable[[bytes, int], int],
    ) -> DecompressResult:
        """Follow a chain of blocks (ZAMN). `resolve_next` reads the 4-byte
        pointer trailing a chained chunk and returns the next block's offset
        within `data`. Called with (data, pointer_offset)."""
        p = self.params
        all_out = bytearray()
        cur = offset
        total_consumed = 0
        while True:
            size, chain, header_bytes = _read_header(data, cur, p)
            block = self.decompress(data, cur)
            all_out += block.data
            total_consumed += block.consumed
            if not chain:
                break
            ptr_off = cur + block.consumed
            cur = resolve_next(data, ptr_off)
            total_consumed += 4
        return DecompressResult(data=bytes(all_out), consumed=total_consumed)

    def compress(self, data: bytes) -> CompressResult:
        """Greedy longest-match compressor. Emits header per params."""
        p = self.params
        ring = bytearray([p.fill_byte] * p.window_size)
        wpos = p.init_pos
        win_mask = p.window_size - 1

        body = bytearray()
        ctrl = 0
        ctrl_bits = 0
        chunk = bytearray()
        src = 0
        n = len(data)

        def flush(final: bool = False):
            nonlocal ctrl, ctrl_bits, chunk
            if ctrl_bits == 0 and not final:
                return
            if ctrl_bits > 0:
                # Pack ctrl into LSB-first byte already; remaining high bits are zero (refs, end)
                body.append(ctrl & 0xFF)
                body.extend(chunk)
                ctrl = 0
                ctrl_bits = 0
                chunk = bytearray()

        while src < n:
            best_len = 0
            best_off = 0
            max_len = min(p.max_match, n - src)
            if max_len >= p.min_match:
                # Search ring buffer for longest match of data[src:src+max_len]
                for off in range(p.window_size):
                    # Match length from offset `off`
                    mlen = 0
                    while mlen < max_len and ring[(off + mlen) & win_mask] == data[src + mlen]:
                        mlen += 1
                    if mlen > best_len:
                        best_len = mlen
                        best_off = off
                        if mlen == max_len:
                            break

            if best_len >= p.min_match:
                # Emit reference: ctrl bit 0
                lo = best_off & 0xFF
                hi = ((best_off >> 8) & 0x0F) << 4 | ((best_len - p.min_match) & 0x0F)
                chunk.append(lo)
                chunk.append(hi)
                for i in range(best_len):
                    ring[wpos] = data[src + i]
                    wpos = (wpos + 1) & win_mask
                src += best_len
            else:
                # Emit literal: ctrl bit 1
                ctrl |= (1 << ctrl_bits)
                chunk.append(data[src])
                ring[wpos] = data[src]
                wpos = (wpos + 1) & win_mask
                src += 1

            ctrl_bits += 1
            if ctrl_bits == 8:
                flush()

        flush(final=True)

        out = bytearray()
        if p.header == "u16_le":
            out.append(len(body) & 0xFF)
            out.append((len(body) >> 8) & 0xFF)
        elif p.header == "u16_le_chain15":
            sz = len(body) & 0x7FFF
            out.append(sz & 0xFF)
            out.append((sz >> 8) & 0xFF)
        # else 'none': no header
        out.extend(body)

        return CompressResult(data=bytes(out), original_size=n)

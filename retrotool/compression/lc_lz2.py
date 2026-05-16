"""LC_LZ2 codec — Lunar Compress's LC_LZ2 / SMW / LTTP / Yoshi's Island variant.

Used by Marvelous ~Another Treasure Island~ and many other first/second-party
SNES titles. Format spec: sneslab.net/wiki/LC_LZ2.

Each compressed chunk has a 1-byte header `CCCLLLLL`:
  CCC  = command (3 bits)
  LLLLL = length-1 (5 bits) — encoded length is `L + 1` bytes (range 1..32)

A header byte of 0xFF terminates decompression.

Standard commands (CCC):
  000  Direct copy            — followed by `len` literal bytes
  001  Byte fill              — followed by 1 byte, repeated `len` times
  010  Word fill              — followed by 2 bytes, alternated for `len` bytes
  011  Increasing fill        — followed by 1 byte, written `len` times incrementing by 1 each step
  100  Repeat                 — followed by big-endian u16 source addr in the output buffer; copies `len` bytes from there
  101  (unused — encoder must not emit; decoders treat as Repeat for safety)

Length extensions (when `len > 32`):

  110CCC00  LLLLLLLL  LLLLLLLL    "16-bit length" — three-byte header.
                                    Real command in the inner CCC field;
                                    16-bit length follows (`L + 1`, range 33..65536).
  111CCCLL  LLLLLLLL              "10-bit length" — two-byte header.
                                    Real command in inner CCC; 10-bit length packed
                                    across the two bytes (`L + 1`, range 33..1024).

The encoder picks the smallest header that fits the chosen length.
The decoder accepts any length encoding that produces the same chunk semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from retrotool.compression.base import Codec, CompressResult, DecompressResult


@dataclass(frozen=True)
class LCLZ2Params:
    """No tunables today — LC_LZ2 is a fixed format. Kept for parity with
    the LZSS / RLE codec shapes (in case future variants need a switch)."""


PARAMS_DEFAULT = LCLZ2Params()


# Standard 5-bit-length form has max length 32. Anything larger needs the
# 10-bit (max 1024) or 16-bit (max 65536) length extension.
_MAX_STD_LEN = 32
_MAX_10BIT_LEN = 1024
_MAX_16BIT_LEN = 65536

# Maximum back-reference window for the Repeat command. The 16-bit address
# field can name any prior position in the output buffer up to 0xFFFF.
_MAX_BACKREF_ADDR = 0xFFFF

# Minimum back-reference length the encoder considers worthwhile. The
# Repeat header itself costs 3 bytes (1 hdr + 2 addr) for short forms, so a
# match of length < 4 either ties or loses against a direct copy that would
# include those bytes for free.
_MIN_BACKREF_LEN = 4


CMD_DIRECT = 0b000
CMD_BYTE_FILL = 0b001
CMD_WORD_FILL = 0b010
CMD_INC_FILL = 0b011
CMD_REPEAT = 0b100


class LCLZ2Codec(Codec):
    """LC_LZ2 / SMW-family compression codec.

    Decoder follows the spec strictly. Encoder is greedy: at each output
    position, it considers byte-fill, word-fill, increasing-fill, and the
    longest back-reference match found in `data[:pos]`, then emits whichever
    chunk has the lowest cost-per-byte. Falls back to direct copy when no
    structured chunk wins.
    """

    name = "lc-lz2"

    def __init__(self, params: LCLZ2Params = PARAMS_DEFAULT):
        self.params = params

    # ---- decompress -------------------------------------------------------

    def decompress(self, data: bytes, offset: int = 0) -> DecompressResult:
        out = bytearray()
        src = offset
        n = len(data)

        while src < n:
            h = data[src]
            src += 1
            if h == 0xFF:
                break

            cmd = (h >> 5) & 7
            if cmd == 0b111:
                # 10-bit length: 111CCCLL  LLLLLLLL
                cmd = (h >> 2) & 7
                if src >= n:
                    raise ValueError("LC_LZ2: truncated 10-bit length header")
                length = (((h & 0x03) << 8) | data[src]) + 1
                src += 1
            elif cmd == 0b110:
                # 16-bit length: 110CCC00  LLLLLLLL  LLLLLLLL
                cmd = (h >> 2) & 7
                if src + 1 >= n:
                    raise ValueError("LC_LZ2: truncated 16-bit length header")
                length = ((data[src] << 8) | data[src + 1]) + 1
                src += 2
            else:
                length = (h & 0x1F) + 1

            if cmd == CMD_DIRECT:
                if src + length > n:
                    raise ValueError("LC_LZ2: truncated direct-copy payload")
                out.extend(data[src:src + length])
                src += length
            elif cmd == CMD_BYTE_FILL:
                if src >= n:
                    raise ValueError("LC_LZ2: truncated byte-fill payload")
                out.extend(bytes([data[src]]) * length)
                src += 1
            elif cmd == CMD_WORD_FILL:
                if src + 1 >= n:
                    raise ValueError("LC_LZ2: truncated word-fill payload")
                pair = (data[src], data[src + 1])
                src += 2
                # Alternate p[0], p[1], p[0], p[1] ... for `length` bytes total.
                for i in range(length):
                    out.append(pair[i & 1])
            elif cmd == CMD_INC_FILL:
                if src >= n:
                    raise ValueError("LC_LZ2: truncated increasing-fill payload")
                start = data[src]
                src += 1
                for i in range(length):
                    out.append((start + i) & 0xFF)
            else:
                # Repeat (CMD_REPEAT, plus 0b101/0b110/0b111 fall-through for
                # decoder-side safety per the spec note).
                if src + 1 >= n:
                    raise ValueError("LC_LZ2: truncated repeat header")
                addr = (data[src] << 8) | data[src + 1]
                src += 2
                if addr + length > len(out):
                    # Some implementations allow self-overlapping copies (where
                    # the source range extends into bytes we're about to
                    # write). Walk byte-by-byte so the just-written tail is
                    # readable on the next iteration.
                    for i in range(length):
                        out.append(out[addr + i])
                else:
                    out.extend(out[addr:addr + length])

        return DecompressResult(data=bytes(out), consumed=src - offset)

    # ---- compress ---------------------------------------------------------

    def compress(self, data: bytes) -> CompressResult:
        out = bytearray()
        n = len(data)
        pos = 0
        # Pending direct-copy run — flushed when a structured chunk wins.
        pending_lit_start = 0

        while pos < n:
            chunk = self._best_chunk(data, pos)
            if chunk is None:
                pos += 1
                continue
            cmd, length, payload, advance = chunk
            # Anything not absorbed by `chunk` since the last flush goes out
            # as direct-copy bytes first.
            if pending_lit_start < pos:
                _emit_literal_run(out, data[pending_lit_start:pos])
            _emit_chunk(out, cmd, length, payload)
            pos += advance
            pending_lit_start = pos

        # Trailing literal run.
        if pending_lit_start < n:
            _emit_literal_run(out, data[pending_lit_start:n])

        out.append(0xFF)  # terminator
        return CompressResult(data=bytes(out), original_size=n)

    def _best_chunk(self, data: bytes, pos: int):
        """Return the structured chunk that compresses the most bytes at `pos`.

        Returns `(cmd, length, payload_bytes, advance)` or `None` to fall
        through to a literal byte. `advance` is how far to step `pos`
        (always equals `length` for our four structured commands; literals
        are handled by the caller).
        """
        n = len(data)
        candidates = []

        # Byte fill: longest run of identical bytes.
        b = data[pos]
        run = 1
        while pos + run < n and data[pos + run] == b and run < _MAX_16BIT_LEN:
            run += 1
        if run >= 2:
            candidates.append((CMD_BYTE_FILL, run, bytes([b]), run))

        # Word fill: longest alternating two-byte pattern starting at `pos`.
        if pos + 1 < n:
            b0, b1 = data[pos], data[pos + 1]
            wrun = 2
            while pos + wrun < n and wrun < _MAX_16BIT_LEN:
                expected = b0 if (wrun & 1) == 0 else b1
                if data[pos + wrun] != expected:
                    break
                wrun += 1
            # Only worth it when `b0 != b1` (else byte-fill is strictly better).
            if wrun >= 3 and b0 != b1:
                candidates.append((CMD_WORD_FILL, wrun, bytes([b0, b1]), wrun))

        # Increasing fill: longest run where each byte is prior+1 mod 256.
        irun = 1
        while pos + irun < n and irun < _MAX_16BIT_LEN \
                and data[pos + irun] == ((b + irun) & 0xFF):
            irun += 1
        if irun >= 3:
            candidates.append((CMD_INC_FILL, irun, bytes([b]), irun))

        # Repeat (back-reference): longest match of `data[pos:]` anywhere in
        # `data[:pos]`. This is the expensive part — the dominant cost is
        # the linear scan; for SNES asset sizes (typ. < 64 KiB) it is fine.
        match_addr, match_len = _longest_match(data, pos, _MAX_BACKREF_ADDR, _MAX_16BIT_LEN)
        if match_len >= _MIN_BACKREF_LEN:
            candidates.append((
                CMD_REPEAT, match_len,
                bytes([(match_addr >> 8) & 0xFF, match_addr & 0xFF]),
                match_len,
            ))

        if not candidates:
            return None

        # Pick the chunk with the best byte savings.
        # Cost = header bytes + payload bytes; savings = covered_len - cost.
        def savings(c):
            cmd, length, payload, _adv = c
            return length - (_header_cost(length) + len(payload))

        candidates.sort(key=savings, reverse=True)
        best = candidates[0]
        # Only emit a structured chunk if it actually saves bytes against a
        # straight literal copy — same logic ensures we don't emit a 4-byte
        # header for a 4-byte run that nets zero savings.
        if savings(best) <= 0:
            return None
        return best


# ---- chunk emit / size helpers ------------------------------------------


def _header_cost(length: int) -> int:
    """How many header bytes a chunk of this length needs."""
    if length <= _MAX_STD_LEN:
        return 1
    if length <= _MAX_10BIT_LEN:
        return 2
    return 3


def _emit_chunk(out: bytearray, cmd: int, length: int, payload: bytes) -> None:
    """Encode the smallest header that fits `length`, then the payload."""
    if length <= _MAX_STD_LEN:
        out.append((cmd << 5) | ((length - 1) & 0x1F))
    elif length <= _MAX_10BIT_LEN:
        ext = length - 1
        out.append(0xE0 | ((cmd & 7) << 2) | ((ext >> 8) & 0x03))
        out.append(ext & 0xFF)
    else:
        ext = length - 1
        out.append(0xC0 | ((cmd & 7) << 2))
        out.append((ext >> 8) & 0xFF)
        out.append(ext & 0xFF)
    out.extend(payload)


def _emit_literal_run(out: bytearray, run: bytes) -> None:
    """Emit a run of literal bytes as one or more direct-copy chunks."""
    pos = 0
    n = len(run)
    while pos < n:
        chunk_len = min(_MAX_16BIT_LEN, n - pos)
        _emit_chunk(out, CMD_DIRECT, chunk_len, b"")
        out.extend(run[pos:pos + chunk_len])
        pos += chunk_len


# ---- back-reference match search ----------------------------------------


def _longest_match(data: bytes, pos: int, max_addr: int, max_len: int) -> tuple[int, int]:
    """Find the longest prefix of `data[pos:]` that occurs in `data[:pos]`.

    Restricted to source addresses ≤ `max_addr` (the LC_LZ2 Repeat field is
    16-bit, so any byte in the first 64 KiB of output is reachable). Returns
    `(best_addr, best_len)`; `best_len == 0` means no match found.

    Implementation: scan every starting position in `data[:pos]`, take the
    longest prefix-match. O(n²) in pos; for SNES asset sizes it's acceptable
    and avoids the bookkeeping of a hash chain. Drop in a hash-chain or
    suffix-array variant if profiles show this is the bottleneck.
    """
    n = len(data)
    if pos == 0:
        return 0, 0
    haystack_end = min(pos, max_addr + 1)
    needle = data[pos:pos + max_len]
    nlen = len(needle)
    best_addr = 0
    best_len = 0

    # Scan from the most-recent positions first (commonly the best matches),
    # so we find a long match early and can break out faster on remaining
    # candidates that can't beat it.
    for start in range(haystack_end - 1, -1, -1):
        # Quick reject: first byte must match.
        if data[start] != needle[0]:
            continue
        # Compute how long the match runs (allowing overlap into >= pos area
        # since LC_LZ2 decoders read sequentially from the output buffer).
        max_possible = min(nlen, n - pos)
        ml = 1
        while ml < max_possible and data[start + ml] == data[pos + ml]:
            ml += 1
            # Self-overlap: allow source to read bytes that were just written.
            # This is naturally handled because we extend out byte-by-byte at
            # decompress time when addr + len > len(out).
        if ml > best_len:
            best_len = ml
            best_addr = start
            if best_len == max_possible:
                break

    return best_addr, best_len

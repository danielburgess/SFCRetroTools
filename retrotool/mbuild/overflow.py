"""Overflow strategies for variable-length data (scripts, tables) that no
longer fit in their original ROM region after re-encoding.

A strategy decides:
  - what bytes go inline at the original offset, and
  - what tails (if any) get written to a freespace region elsewhere in ROM.

Game-specific mechanics live in strategy plugins. The framework provides:

  - `FreespaceAllocator` — bump-allocator over a list of (lo, hi) byte ranges
    in PC-offset space.
  - `OverflowStrategy` ABC — `pack(entry, allocator) -> Packed` returns the
    inline replacement plus zero or more `(offset, bytes)` tail writes.
  - A pluggable `register / get / list_strategies` registry.
  - Three built-ins:

      * `fail` — overflow is an error (default).
      * `truncate` — drop bytes past `max_inline`. Useful only with a leading
        terminator the consumer respects.
      * `inline-redirect` — write inline_part + marker + 24-bit-LE pointer to
        an allocated tail. Confirmed against LM3's `FF C0 ll mm hh` mechanism.
        The reader follows the pointer and continues consuming until a
        configured terminator (default `\\x00`) or another marker. Optionally
        emits a "redirect-back" suffix in the tail so a trailing inline
        suffix can resume in the original location.

Handler integration (script / fixed-records / etc.) lands in Phase 6c.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional


# ---- freespace allocator --------------------------------------------------

@dataclass
class FreespaceRange:
    """Half-open PC-offset byte range available for overflow data."""
    lo: int
    hi: int  # exclusive

    @property
    def remaining(self) -> int:
        return self.hi - self.lo


class FreespaceExhausted(RuntimeError):
    pass


class FreespaceAllocator:
    """Bump-allocate fixed-size requests across a list of ranges, in order."""

    def __init__(self, ranges: list[FreespaceRange]):
        self._ranges = list(ranges)
        self._cursor = 0  # index into self._ranges

    @classmethod
    def from_pairs(cls, pairs: list[tuple[int, int]]) -> "FreespaceAllocator":
        return cls([FreespaceRange(lo, hi) for lo, hi in pairs])

    def alloc(self, n: int) -> int:
        """Return PC offset where `n` bytes can be written. Bumps the cursor."""
        if n <= 0:
            raise ValueError("alloc(n) requires n > 0")
        while self._cursor < len(self._ranges):
            r = self._ranges[self._cursor]
            if r.remaining >= n:
                off = r.lo
                r.lo += n
                return off
            self._cursor += 1
        raise FreespaceExhausted(f"no freespace range fits {n} bytes")

    def remaining(self) -> int:
        return sum(r.remaining for r in self._ranges[self._cursor:])


# ---- strategy ABC + result ------------------------------------------------

@dataclass
class Entry:
    """One variable-length blob a strategy is asked to place."""
    id: str            # caller's identifier (e.g. "script[42]")
    encoded: bytes     # full encoded bytes of the entry
    max_inline: int    # bytes available at the original offset
    original_offset: Optional[int] = None  # inline write offset (if known)


@dataclass
class TailWrite:
    offset: int
    data: bytes


@dataclass
class Packed:
    """Result of packing one entry."""
    inline: bytes
    tails: list[TailWrite] = field(default_factory=list)
    overflow_used: bool = False


class OverflowStrategy(ABC):
    name: str = ""

    @abstractmethod
    def pack(self, entry: Entry, allocator: Optional[FreespaceAllocator]) -> Packed:
        """Return the inline replacement + any tail writes. Raise on failure."""


# ---- registry -------------------------------------------------------------

_REGISTRY: dict[str, OverflowStrategy] = {}


def register(strategy: OverflowStrategy) -> None:
    if not strategy.name:
        raise ValueError("strategy.name must be set")
    _REGISTRY[strategy.name] = strategy


def get(name: str) -> OverflowStrategy:
    try:
        return _REGISTRY[name]
    except KeyError as e:
        raise KeyError(
            f"unknown overflow strategy {name!r}. Known: {sorted(_REGISTRY)}"
        ) from e


def list_strategies() -> list[str]:
    return sorted(_REGISTRY)


# ---- built-in strategies --------------------------------------------------

class FailStrategy(OverflowStrategy):
    name = "fail"

    def pack(self, entry: Entry, allocator: Optional[FreespaceAllocator]) -> Packed:
        if len(entry.encoded) > entry.max_inline:
            raise OverflowError(
                f"{entry.id}: encoded {len(entry.encoded)}b > max_inline "
                f"{entry.max_inline}b (strategy=fail)"
            )
        return Packed(inline=entry.encoded)


class TruncateStrategy(OverflowStrategy):
    name = "truncate"

    def pack(self, entry: Entry, allocator: Optional[FreespaceAllocator]) -> Packed:
        return Packed(inline=entry.encoded[:entry.max_inline])


class InlineRedirectStrategy(OverflowStrategy):
    """Game-agnostic version of LM3's `FF C0 ll mm hh` redirect.

    Inline replacement layout::

        [inline_part] [marker] [pointer-bytes]

    where:
      - `inline_part` is `entry.encoded[:split]`, with `split` chosen by
        `splitter` (default: greedy — largest split that leaves room for the
        marker+pointer).
      - `marker` is the configurable byte sequence (default `b"\\xFF\\xC0"`).
      - `pointer` is the allocated tail address encoded by `pointer_encoder`
        (default: 24-bit LoROM SNES address, little-endian).

    Tail layout::

        [encoded[split:]]                       (default)
        [encoded[split:]] [marker] [orig_resume]   (if redirect_back=True)

    `redirect_back` lets a strategy resume in the original ROM region after
    the spilled portion (LM3's event-script case where the terminating bytes
    must stay put because of hard-coded references).
    """

    name = "inline-redirect"

    def __init__(
        self,
        *,
        marker: bytes = b"\xFF\xC0",
        pointer_size: int = 3,
        pointer_encoder: Optional[Callable[[int], bytes]] = None,
        splitter: Optional[Callable[[bytes, int], int]] = None,
        redirect_back: bool = False,
    ):
        self.marker = bytes(marker)
        self.pointer_size = pointer_size
        self.pointer_encoder = pointer_encoder or _default_pc_to_lorom1_le
        self.splitter = splitter
        self.redirect_back = redirect_back

    @property
    def stub_size(self) -> int:
        return len(self.marker) + self.pointer_size

    def pack(self, entry: Entry, allocator: Optional[FreespaceAllocator]) -> Packed:
        if len(entry.encoded) <= entry.max_inline:
            return Packed(inline=entry.encoded)
        if allocator is None:
            raise ValueError(
                f"{entry.id}: inline-redirect needs an allocator for overflow tail"
            )
        if entry.max_inline < self.stub_size:
            raise OverflowError(
                f"{entry.id}: max_inline {entry.max_inline} < redirect stub "
                f"size {self.stub_size}"
            )

        budget = entry.max_inline - self.stub_size
        if self.splitter is not None:
            split = self.splitter(entry.encoded, budget)
        else:
            split = budget
        split = max(0, min(split, budget))

        inline_part = entry.encoded[:split]
        tail = entry.encoded[split:]
        if self.redirect_back:
            if entry.original_offset is None:
                raise ValueError(
                    f"{entry.id}: redirect_back=True requires original_offset"
                )
            # After the spilled portion, jump back to where the inline part
            # left off in the original region.
            resume_pc = entry.original_offset + entry.max_inline
            tail = tail + self.marker + self.pointer_encoder(resume_pc)

        tail_pc = allocator.alloc(len(tail))
        ptr = self.pointer_encoder(tail_pc)
        if len(ptr) != self.pointer_size:
            raise ValueError(
                f"{entry.id}: pointer_encoder returned {len(ptr)}b, "
                f"expected {self.pointer_size}b"
            )
        inline = inline_part + self.marker + ptr
        return Packed(
            inline=inline,
            tails=[TailWrite(offset=tail_pc, data=tail)],
            overflow_used=True,
        )


def split_at_last_marker_byte(marker_byte: int) -> Callable[[bytes, int], int]:
    """Splitter that backs the split up to the last `marker_byte` within budget.

    Use when the encoded format has window-boundary bytes (LM3's `[P]` = 0x10)
    where redirects are valid. Returns split index = `(last marker idx) + 1`,
    or 0 if none found within budget.
    """
    def _splitter(encoded: bytes, budget: int) -> int:
        window = encoded[:budget]
        idx = window.rfind(bytes([marker_byte]))
        if idx < 0:
            return 0
        return idx + 1
    return _splitter


def _default_pc_to_lorom1_le(pc: int) -> bytes:
    """24-bit little-endian SNES (LoROM) address for a given PC offset."""
    # LoROM mapping: PC = ((bank & 0x7F) << 15) | (addr & 0x7FFF)
    # → bank = (pc >> 15) | 0x80, addr = (pc & 0x7FFF) | 0x8000
    bank = ((pc >> 15) & 0x7F) | 0x80
    addr = (pc & 0x7FFF) | 0x8000
    snes = (bank << 16) | addr
    return bytes([snes & 0xFF, (snes >> 8) & 0xFF, (snes >> 16) & 0xFF])


# Auto-register built-ins at import time.
register(FailStrategy())
register(TruncateStrategy())
register(InlineRedirectStrategy())

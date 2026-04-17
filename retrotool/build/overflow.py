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

    def reserve(self, off: int, n: int) -> bool:
        """Mark `[off, off+n)` as consumed if it lies inside a freespace range.

        Used when replaying cached writes: the cache already holds an absolute
        PC that was allocated in a prior build, so the cursor must be advanced
        past it or a later `alloc()` will hand out the same bytes. Only
        advances monotonically (never reclaims gaps below the cursor).

        Returns True if the write intersected a freespace range, else False.
        """
        if n <= 0:
            return False
        end = off + n
        for idx, r in enumerate(self._ranges):
            if off < r.hi and end > r.lo:
                if end > r.lo:
                    r.lo = min(r.hi, max(r.lo, end))
                if r.lo >= r.hi and idx >= self._cursor:
                    self._cursor = max(self._cursor, idx + 1)
                return True
        return False

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
class PackFixup:
    """Deferred pointer resolution for an overflow redirect.

    `inline_offset` is the index within `Packed.inline` where a 3-byte
    little-endian SNES pointer should be written. `target_pc` is the PC
    offset the caller must encode via its address scheme. Strategies that
    defer pointer baking emit these instead of writing the pointer inline.
    """
    inline_offset: int
    target_pc: int


@dataclass
class Packed:
    """Result of packing one entry.

    `source_split` is how many bytes of `entry.encoded` were consumed by the
    inline portion. Callers mapping per-entry fixup offsets (that originated
    inside `entry.encoded`) from source-offset to final rom-PC use this to
    decide whether a given source offset landed in the inline write or in
    the first tail write. Strategies that don't split set it to
    `len(entry.encoded)` (everything is inline).
    """
    inline: Optional[bytes]  # None when preserve_source=True
    tails: list[TailWrite] = field(default_factory=list)
    overflow_used: bool = False
    fixups: list[PackFixup] = field(default_factory=list)
    source_split: int = 0
    # Strategy signals the caller to leave the original ROM bytes intact for
    # this entry (e.g. slot smaller than any redirect stub). Used by inline-
    # redirect when max_inline < stub_size — mirrors lm3.py "skip & preserve"
    # behavior for shared-region entries reached only via external pins.
    preserve_source: bool = False


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
        return Packed(inline=entry.encoded, source_split=len(entry.encoded))


class TruncateStrategy(OverflowStrategy):
    name = "truncate"

    def pack(self, entry: Entry, allocator: Optional[FreespaceAllocator]) -> Packed:
        inline = entry.encoded[:entry.max_inline]
        return Packed(inline=inline, source_split=len(inline))


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
        defer_pointer: bool = False,
    ):
        self.marker = bytes(marker)
        self.pointer_size = pointer_size
        self.pointer_encoder = pointer_encoder or _default_pc_to_lorom1_le
        self.splitter = splitter
        self.redirect_back = redirect_back
        # When True, the strategy writes a 3-byte `\xFF\xFF\xFF` placeholder at
        # the pointer slot and returns a `PackFixup` in `Packed.fixups`. The
        # caller (handle_script) resolves all fixups uniformly after every
        # entry is placed, letting global labels and cross-entry refs share
        # one code path.
        self.defer_pointer = defer_pointer

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
            # Slot too small for an FFC0 redirect stub. Mirror lm3.py
            # behavior: preserve the source ROM bytes for this entry (the
            # game typically reaches such entries only via external pins,
            # so overwriting them would break cross-entry redirects).
            return Packed(inline=None, preserve_source=True)

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
        marker_off = len(inline_part)
        ptr_off = marker_off + len(self.marker)
        if self.defer_pointer:
            placeholder = b"\xFF" * self.pointer_size
            inline = inline_part + self.marker + placeholder
            return Packed(
                inline=inline,
                tails=[TailWrite(offset=tail_pc, data=tail)],
                overflow_used=True,
                fixups=[PackFixup(inline_offset=ptr_off, target_pc=tail_pc)],
                source_split=split,
            )
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
            source_split=split,
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


# ---- pointer-encoder registry --------------------------------------------
#
# Named callables that turn a PC offset into the bytes a game's reader
# interprets as an address. Strategies and handler fixup resolution pull
# from here so config files can stay host/game-agnostic.

PointerEncoder = Callable[[int], bytes]

_POINTER_ENCODERS: dict[str, PointerEncoder] = {}


def register_pointer_encoder(name: str, fn: PointerEncoder) -> None:
    _POINTER_ENCODERS[name] = fn


def get_pointer_encoder(name: str) -> PointerEncoder:
    try:
        return _POINTER_ENCODERS[name]
    except KeyError as e:
        raise KeyError(
            f"unknown pointer-encoder {name!r}. "
            f"Known: {sorted(_POINTER_ENCODERS)}"
        ) from e


def list_pointer_encoders() -> list[str]:
    return sorted(_POINTER_ENCODERS)


def _pc_to_snes_lorom1_24le(pc: int) -> bytes:
    """24-bit LoROM1 SNES address, little-endian. bank = ((pc>>15)&0x7F)|0x80."""
    return _default_pc_to_lorom1_le(pc)


def _pc_to_snes_lorom0_24le(pc: int) -> bytes:
    """24-bit LoROM0 SNES address, little-endian. bank = (pc>>15)&0x7F (no $80 bit)."""
    bank = (pc >> 15) & 0x7F
    addr = (pc & 0x7FFF) | 0x8000
    snes = (bank << 16) | addr
    return bytes([snes & 0xFF, (snes >> 8) & 0xFF, (snes >> 16) & 0xFF])


def _pc_to_hirom_24le(pc: int) -> bytes:
    """24-bit HiROM SNES address, little-endian. bank = (pc>>16)|0xC0."""
    bank = ((pc >> 16) & 0x3F) | 0xC0
    addr = pc & 0xFFFF
    snes = (bank << 16) | addr
    return bytes([snes & 0xFF, (snes >> 8) & 0xFF, (snes >> 16) & 0xFF])


register_pointer_encoder("snes-lorom1-24le", _pc_to_snes_lorom1_24le)
register_pointer_encoder("snes-lorom0-24le", _pc_to_snes_lorom0_24le)
register_pointer_encoder("snes-hirom-24le", _pc_to_hirom_24le)


# ---- splitter registry ---------------------------------------------------
#
# A splitter is a factory: `make(arg) -> (encoded, budget) -> split_index`.
# `arg` comes from config (`splitter-arg`) and is splitter-specific.

SplitterFactory = Callable[..., Callable[[bytes, int], int]]

_SPLITTERS: dict[str, SplitterFactory] = {}


def register_splitter(name: str, factory: SplitterFactory) -> None:
    _SPLITTERS[name] = factory


def build_splitter(name: str, arg: object = None, ctx: Optional[dict] = None) -> Callable[[bytes, int], int]:
    """Build splitter by name. `ctx` is host-supplied context (e.g. table
    `ctrl_lengths`). Factories ignore kwargs they don't need."""
    factory = get_splitter(name)
    try:
        return factory(arg, ctx=ctx or {})
    except TypeError:
        # Back-compat: factories pre-dating the `ctx` kwarg.
        return factory(arg)


def get_splitter(name: str) -> SplitterFactory:
    try:
        return _SPLITTERS[name]
    except KeyError as e:
        raise KeyError(
            f"unknown splitter {name!r}. Known: {sorted(_SPLITTERS)}"
        ) from e


def list_splitters() -> list[str]:
    return sorted(_SPLITTERS)


def _splitter_greedy_factory(arg: object, *, ctx: Optional[dict] = None) -> Callable[[bytes, int], int]:
    """Default: consume the full budget."""
    return lambda encoded, budget: budget


def _splitter_at_last_marker_byte_factory(arg: object, *, ctx: Optional[dict] = None) -> Callable[[bytes, int], int]:
    if arg is None:
        raise ValueError("splitter 'at-last-marker-byte' requires splitter-arg (byte value)")
    if not isinstance(arg, int):
        raise ValueError("splitter-arg for 'at-last-marker-byte' must be an int")
    return split_at_last_marker_byte(arg)


def split_ctrl_aware(
    ctrl_lengths: dict,
    *,
    default_length: int = 2,
    terminator: Optional[int] = 0x00,
    event_script: bool = False,
    text_enter: int = 0x10,
) -> Callable[[bytes, int], int]:
    """Latest safe byte-boundary split that respects multi-byte FF control
    codes. Mirrors LM3 `_find_safe_split`: walks `encoded`, tracking
    last position that lands between complete tokens. For non-event data,
    stops at the first `terminator` (entry end); for event-script data,
    returns the last safe split inside a text window ([text_enter]..[term]).
    """
    def _splitter(encoded: bytes, budget: int) -> int:
        if budget <= 0:
            return 0
        pos = 0
        last_safe = 0
        in_text = False
        last_text_safe = 0
        n = len(encoded)
        while pos < n:
            b = encoded[pos]
            if b == 0xFF and pos + 1 < n:
                sub = encoded[pos + 1]
                cl = ctrl_lengths.get(sub, default_length)
                if pos + cl <= budget:
                    last_safe = pos + cl
                    if event_script and in_text:
                        last_text_safe = pos + cl
                    pos += cl
                else:
                    break
            elif terminator is not None and b == terminator:
                if event_script:
                    if pos + 1 <= budget:
                        if in_text:
                            in_text = False
                        last_safe = pos + 1
                        pos += 1
                    else:
                        break
                else:
                    break
            elif event_script and b == text_enter:
                in_text = True
                if pos + 1 <= budget:
                    last_safe = pos + 1
                    pos += 1
                else:
                    break
            else:
                if pos + 1 <= budget:
                    last_safe = pos + 1
                    if event_script and in_text:
                        last_text_safe = pos + 1
                    pos += 1
                else:
                    break
        return last_text_safe if event_script else last_safe
    return _splitter


def _splitter_ctrl_aware_factory(arg: object, *, ctx: Optional[dict] = None) -> Callable[[bytes, int], int]:
    """Ctrl-aware splitter. Uses `ctx['ctrl_lengths']` provided by the caller
    (handler builds it from the loaded Table). `arg` may be a dict overriding
    `event_script`/`terminator`/`text_enter`/`default_length`.
    """
    ctx = ctx or {}
    ctrl_lengths = ctx.get("ctrl_lengths") or {}
    opts = arg if isinstance(arg, dict) else {}
    return split_ctrl_aware(
        ctrl_lengths,
        default_length=int(opts.get("default-length", 2)),
        terminator=opts.get("terminator", 0x00),
        event_script=bool(opts.get("event-script", ctx.get("event_script", False))),
        text_enter=int(opts.get("text-enter", 0x10)),
    )


register_splitter("greedy", _splitter_greedy_factory)
register_splitter("at-last-marker-byte", _splitter_at_last_marker_byte_factory)
register_splitter("ctrl-aware", _splitter_ctrl_aware_factory)


# ---- config factory ------------------------------------------------------

def _coerce_bytes(val: object, key: str) -> bytes:
    if isinstance(val, (bytes, bytearray)):
        return bytes(val)
    if isinstance(val, list) and all(isinstance(b, int) for b in val):
        return bytes(val)
    if isinstance(val, str):
        s = val.strip().lower()
        if s.startswith("0x"):
            s = s[2:]
        return bytes.fromhex(s)
    raise ValueError(f"{key}: unsupported form {val!r} (want list[int] or hex string)")


def strategy_from_config(cfg: dict, *, splitter_ctx: Optional[dict] = None) -> OverflowStrategy:
    """Build a strategy instance from a parsed config dict.

    Supported keys (all optional except `strategy`):
      - `strategy`         strategy name (required; e.g. 'inline-redirect')
      - `marker`           bytes; list[int] or hex string
      - `pointer-size`     int (default 3)
      - `pointer-encoder`  registered name (default 'snes-lorom1-24le')
      - `splitter`         registered name (default 'greedy')
      - `splitter-arg`     int or other; forwarded to splitter factory
      - `redirect-back`    bool
      - `defer-pointer`    bool (default True — lets the handler resolve the
                           pointer after every entry is placed)

    Strategies not needing these just look them up in the registry.
    """
    if not isinstance(cfg, dict):
        raise ValueError(f"overflow config must be a dict, got {type(cfg).__name__}")
    name = cfg.get("strategy")
    if not name:
        raise ValueError("overflow config: 'strategy' is required")

    # Built-in strategies that accept inline/keyword params get constructed
    # here; anything else falls back to the name-keyed registry singleton.
    if name == "inline-redirect":
        marker = _coerce_bytes(cfg.get("marker", b"\xFF\xC0"), "overflow.marker")
        pointer_size = int(cfg.get("pointer-size", 3))
        enc_name = cfg.get("pointer-encoder", "snes-lorom1-24le")
        pointer_encoder = get_pointer_encoder(str(enc_name))
        split_name = cfg.get("splitter", "greedy")
        splitter = build_splitter(str(split_name), cfg.get("splitter-arg"), ctx=splitter_ctx)
        redirect_back = bool(cfg.get("redirect-back", False))
        defer_pointer = bool(cfg.get("defer-pointer", True))
        return InlineRedirectStrategy(
            marker=marker,
            pointer_size=pointer_size,
            pointer_encoder=pointer_encoder,
            splitter=splitter,
            redirect_back=redirect_back,
            defer_pointer=defer_pointer,
        )
    return get(name)


# Auto-register built-ins at import time.
register(FailStrategy())
register(TruncateStrategy())
register(InlineRedirectStrategy())

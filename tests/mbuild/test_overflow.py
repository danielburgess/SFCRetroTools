"""Phase 6b overflow strategies — registry + built-ins (fail, truncate,
inline-redirect). Handler integration lands in Phase 6c."""
from __future__ import annotations

import pytest

from retrotool.mbuild.overflow import (
    Entry,
    FailStrategy,
    FreespaceAllocator,
    FreespaceExhausted,
    InlineRedirectStrategy,
    TruncateStrategy,
    _default_pc_to_lorom1_le,
    get,
    list_strategies,
    split_at_last_marker_byte,
)


def test_registry_built_ins():
    assert {"fail", "truncate", "inline-redirect"}.issubset(set(list_strategies()))
    assert isinstance(get("fail"), FailStrategy)


def test_freespace_allocator_bumps_through_ranges():
    a = FreespaceAllocator.from_pairs([(0x100, 0x110), (0x200, 0x300)])
    assert a.alloc(8) == 0x100
    assert a.alloc(8) == 0x108
    assert a.alloc(0x10) == 0x200    # rolled to next range
    assert a.remaining() == 0xF0


def test_freespace_exhausted():
    a = FreespaceAllocator.from_pairs([(0, 4)])
    a.alloc(4)
    with pytest.raises(FreespaceExhausted):
        a.alloc(1)


def test_fail_strategy_passes_short_entry():
    s = FailStrategy()
    p = s.pack(Entry("e", b"abc", max_inline=8), allocator=None)
    assert p.inline == b"abc"
    assert not p.overflow_used


def test_fail_strategy_raises_on_overflow():
    with pytest.raises(OverflowError):
        FailStrategy().pack(Entry("e", b"x" * 10, max_inline=4), None)


def test_truncate_strategy_drops_excess():
    p = TruncateStrategy().pack(Entry("e", b"abcdef", max_inline=3), None)
    assert p.inline == b"abc"


def test_inline_redirect_no_overflow_returns_passthrough():
    p = InlineRedirectStrategy().pack(Entry("e", b"abc", max_inline=8), None)
    assert p.inline == b"abc"
    assert not p.overflow_used
    assert p.tails == []


def test_inline_redirect_writes_marker_and_pointer():
    """LM3 shape: 6 bytes encoded = 1 byte inline + FF C0 + 3-byte LE addr."""
    alloc = FreespaceAllocator.from_pairs([(0x30000, 0x40000)])  # bank $86 LoROM
    s = InlineRedirectStrategy()  # default marker=FF C0, 3-byte LoROM ptr
    encoded = b"\x10ABCDEFGHIJ"  # 11 bytes
    p = s.pack(Entry("e", encoded, max_inline=6, original_offset=0x1000), alloc)
    assert p.overflow_used
    # inline = encoded[:1] + FF C0 + ptr  (6 bytes total)
    assert p.inline[:1] == b"\x10"
    assert p.inline[1:3] == b"\xFF\xC0"
    assert len(p.inline) == 6
    # pointer points to the allocated tail
    ptr = p.inline[3:6]
    expected = _default_pc_to_lorom1_le(0x30000)
    assert ptr == expected
    # tail = encoded[1:]
    assert p.tails[0].offset == 0x30000
    assert p.tails[0].data == encoded[1:]


def test_inline_redirect_with_window_splitter():
    """LM3 event-script: split at last 0x10 byte within budget."""
    alloc = FreespaceAllocator.from_pairs([(0x30000, 0x40000)])
    s = InlineRedirectStrategy(splitter=split_at_last_marker_byte(0x10))
    # Window-boundary bytes at indices 0, 4, 9. Budget = max_inline - stub = 8.
    encoded = b"\x10AAA\x10BBBB\x10CCCC"  # 14 bytes; last 0x10 ≤ idx 7 is at 4
    p = s.pack(Entry("e", encoded, max_inline=13, original_offset=0x1000), alloc)
    # Split index = 5 (idx 4 + 1) → 5 bytes inline + 5-byte stub = 10 inline
    assert p.inline[:5] == b"\x10AAA\x10"
    assert p.inline[5:7] == b"\xFF\xC0"
    assert p.tails[0].data == encoded[5:]


def test_inline_redirect_redirect_back_appends_resume_pointer():
    alloc = FreespaceAllocator.from_pairs([(0x30000, 0x40000)])
    s = InlineRedirectStrategy(redirect_back=True)
    encoded = b"\x10AAAAAA\x00"  # 8 bytes
    # max_inline=6 → budget=1, split=1 → inline = "\x10" + FF C0 + ptr
    p = s.pack(Entry("e", encoded, max_inline=6, original_offset=0x2000), alloc)
    tail = p.tails[0].data
    # tail = encoded[1:] + FF C0 + ptr-back-to-(orig+max_inline)
    assert tail.startswith(encoded[1:])
    assert tail[len(encoded) - 1:len(encoded) + 1] == b"\xFF\xC0"
    resume_ptr = tail[len(encoded) + 1:]
    assert resume_ptr == _default_pc_to_lorom1_le(0x2000 + 6)


def test_inline_redirect_requires_allocator_for_overflow():
    s = InlineRedirectStrategy()
    with pytest.raises(ValueError, match="allocator"):
        s.pack(Entry("e", b"x" * 10, max_inline=5), None)


def test_inline_redirect_rejects_too_small_budget():
    s = InlineRedirectStrategy()  # stub = 5 bytes
    alloc = FreespaceAllocator.from_pairs([(0, 0x100)])
    with pytest.raises(OverflowError, match="stub"):
        s.pack(Entry("e", b"x" * 10, max_inline=4), alloc)


def test_inline_redirect_defer_pointer_emits_fixup():
    from retrotool.mbuild.overflow import PackFixup
    alloc = FreespaceAllocator.from_pairs([(0x30000, 0x40000)])
    s = InlineRedirectStrategy(defer_pointer=True)
    encoded = b"\x10ABCDEFGHIJ"  # 11 bytes
    p = s.pack(Entry("e", encoded, max_inline=6, original_offset=0x1000), alloc)
    assert p.overflow_used
    assert p.inline[1:3] == b"\xFF\xC0"
    # placeholder, not the real ptr
    assert p.inline[3:6] == b"\xFF\xFF\xFF"
    # fixup points at the 3-byte slot and the allocated tail
    assert p.fixups == [PackFixup(inline_offset=3, target_pc=0x30000)]

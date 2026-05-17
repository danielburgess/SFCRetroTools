"""Multi-prefix support for `split_ctrl_aware` (Step 3 of the rbshura
retrotool-merge plan).

Coverage:
  - Legacy single-prefix shape (`ctrl_lengths` only) still works.
  - Multi-prefix `ctrl_table` walks each prefix with its own lengths.
  - Per-cmd overrides honored (e.g. FC.02=4 in rbshura).
  - 1-byte standalone ctrl that doubles as the terminator (rbshura: 0xFF).
  - Budget cuts inside multi-byte ctrl sequences land on the prior safe
    boundary, not mid-sequence.
"""
from __future__ import annotations

import pytest

from retrotool.build.overflow import split_ctrl_aware


# --------------------------------------------------------------------------
# Legacy single-prefix shape (LM3-era contract)
# --------------------------------------------------------------------------

def test_legacy_single_prefix_advances_through_ff_ctrl():
    # FF 80 = 2-byte ctrl; 0x00 = terminator.
    split = split_ctrl_aware(
        ctrl_lengths={0x80: 2}, default_length=2, terminator=0x00
    )
    # bytes: AA BB FF 80 CC 00  → walk should stop at the 0x00 (terminator)
    encoded = bytes([0xAA, 0xBB, 0xFF, 0x80, 0xCC, 0x00])
    # Budget large enough to cover everything before terminator.
    assert split(encoded, 5) == 5  # AA BB FF80 CC ← all safe pre-term
    # Budget cuts mid-ctrl: last_safe stays at end of previous token.
    assert split(encoded, 4) == 4  # AA BB FF80 fits; CC would be 5
    assert split(encoded, 3) == 2  # FF80 spans 3..4 doesn't fit → AA BB


def test_legacy_terminator_breaks_walk():
    split = split_ctrl_aware(
        ctrl_lengths={}, default_length=2, terminator=0x00
    )
    encoded = bytes([0x11, 0x22, 0x00, 0x33])
    # Walk stops at 0x00 — bytes 0x11 0x22 are safe (last_safe=2).
    assert split(encoded, 99) == 2


# --------------------------------------------------------------------------
# Multi-prefix shape (rbshura: F7-FF each their own opcode)
# --------------------------------------------------------------------------

# Mirror rbshura's @ctrl declarations:
#   F7=2, F8=2, F9=2, FB=2, FC=3 (FC.02=4), FD=1, FE=1, FF=1 (=terminator)
RBSHURA_CTRL_TABLE = {
    0xF7: (2, {}),
    0xF8: (2, {}),
    0xF9: (2, {}),
    0xFB: (2, {}),
    0xFC: (3, {0x02: 4}),
    0xFD: (1, {}),
    0xFE: (1, {}),
    0xFF: (1, {}),
}


def test_rbshura_walks_multi_prefix_entry():
    """Walk a real rbshura entry: SPD/FC/text/NL/text/END."""
    split = split_ctrl_aware(
        ctrl_table=RBSHURA_CTRL_TABLE, terminator=0xFF
    )
    # [F8][01] [FC][01][02] AA AA [FD] BB BB [F7][FF] [FF]  (END terminator)
    #  0  1    2  3  4     5  6  7    8  9  10  11   12
    encoded = bytes([
        0xF8, 0x01,        # SPD 01
        0xFC, 0x01, 0x02,  # FC 01 02 (3-byte, NOT the 4-byte FC.02 form)
        0xAA, 0xAA,        # plain text bytes
        0xFD,              # NL
        0xBB, 0xBB,        # more text
        0xF7, 0xFF,        # END opcode (F7 takes a param byte)
        0xFF,              # standalone FF = entry terminator
    ])
    # With ample budget, walk should stop at byte 12 (the terminator FF).
    # last_safe = 12 (everything before the terminator).
    assert split(encoded, 99) == 12


def test_rbshura_fc02_consumes_four_bytes():
    """FC.02 sub-cmd is 4 bytes (FC 02 XX YY), not the default 3."""
    split = split_ctrl_aware(
        ctrl_table=RBSHURA_CTRL_TABLE, terminator=0xFF
    )
    # FC 02 24 08 AA FF  ← FC.02 must consume 4 bytes; budget=4 fits exactly
    encoded = bytes([0xFC, 0x02, 0x24, 0x08, 0xAA, 0xFF])
    assert split(encoded, 4) == 4   # FC.02 fits in budget=4
    assert split(encoded, 3) == 0   # FC.02 needs 4 bytes; budget=3 → cut before it
    assert split(encoded, 5) == 5   # FC.02 + AA fits
    assert split(encoded, 99) == 5  # stops at FF (terminator)


def test_rbshura_split_inside_fc02_falls_back_to_last_safe():
    """If budget would cut between FC 02 and its remaining params, the
    splitter must return the previous token boundary — never the mid-cmd
    offset."""
    split = split_ctrl_aware(
        ctrl_table=RBSHURA_CTRL_TABLE, terminator=0xFF
    )
    # F8 01 FC 02 24 08 AA FF
    #  0  1  2  3  4  5  6  7
    encoded = bytes([0xF8, 0x01, 0xFC, 0x02, 0x24, 0x08, 0xAA, 0xFF])
    # budget=3..5 all land inside FC.02 → must return 2 (end of F8 01)
    assert split(encoded, 3) == 2
    assert split(encoded, 4) == 2
    assert split(encoded, 5) == 2
    assert split(encoded, 6) == 6  # FC.02 (4b) fits at 2..5; ends at 6


def test_rbshura_standalone_fd_fe_one_byte():
    """FD (NL) and FE (PB) are 1-byte standalone — each consumes exactly 1
    byte and does NOT pull a param."""
    split = split_ctrl_aware(
        ctrl_table=RBSHURA_CTRL_TABLE, terminator=0xFF
    )
    encoded = bytes([0xFD, 0xFE, 0xAA, 0xFF])
    assert split(encoded, 99) == 3   # FD FE AA, then stop at FF


def test_rbshura_ff_is_terminator_not_ctrl_prefix_consumed():
    """FF in rbshura is BOTH a declared prefix (default_length=1, no cmds)
    AND the terminator. Walking must treat it as a terminator (entry
    ends here), not advance past it as a ctrl."""
    split = split_ctrl_aware(
        ctrl_table=RBSHURA_CTRL_TABLE, terminator=0xFF
    )
    encoded = bytes([0xAA, 0xBB, 0xFF, 0xCC])
    # Should stop at the FF (offset 2). last_safe = 2 (the AA BB).
    assert split(encoded, 99) == 2


def test_rbshura_f7_with_param_ff_does_not_terminate():
    """`F7 FF` is the 2-byte END opcode mid-entry — F7 consumes FF as its
    param, so the walk continues past it. Only a *standalone* FF (not
    preceded by an active prefix consume) terminates."""
    split = split_ctrl_aware(
        ctrl_table=RBSHURA_CTRL_TABLE, terminator=0xFF
    )
    # AA F7 FF BB FF  ← F7 takes FF as param (bytes 1..2), BB (3), then term FF (4).
    encoded = bytes([0xAA, 0xF7, 0xFF, 0xBB, 0xFF])
    assert split(encoded, 99) == 4   # all bytes up to (not incl) terminator
    # Budget mid-F7-FF must fall back to previous token end.
    assert split(encoded, 2) == 1    # F7-FF needs 2..3; budget=2 → only AA


# --------------------------------------------------------------------------
# ctx-driven factory path
# --------------------------------------------------------------------------

def test_factory_prefers_ctrl_table_over_ctrl_lengths():
    """`_splitter_ctrl_aware_factory` should pick ctrl_table from ctx when
    both shapes are present (the multi-prefix world is richer)."""
    from retrotool.build.overflow import _splitter_ctrl_aware_factory
    ctx = {
        "ctrl_lengths": {0x80: 2},          # legacy single-prefix shape
        "ctrl_table": RBSHURA_CTRL_TABLE,    # preferred
        "terminator": 0xFF,
    }
    split = _splitter_ctrl_aware_factory(None, ctx=ctx)
    encoded = bytes([0xFC, 0x02, 0x24, 0x08, 0xAA, 0xFF])
    # If factory used ctrl_lengths (FF as default 2-byte ctrl + term=0xFF
    # mismatch), it would walk wrong. ctrl_table path stops at FF (term).
    assert split(encoded, 99) == 5  # FC.02 + AA, then term

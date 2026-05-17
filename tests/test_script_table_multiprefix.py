"""Tests for multi-prefix @ctrl_prefix support in retrotool.script.table.

Games like Rushing Beat Shura use multiple top-level opcode bytes
(F7, F8, F9, FA, FB, FC, FD, FE, FF) — each is its own control code with
its own per-cmd-byte length, not a `prefix + cmd` scheme under a single
shared prefix. This file exercises that model end-to-end.
"""
from __future__ import annotations

from pathlib import Path

from retrotool.script.table import Table


def _write_table(tmp_path: Path, body: str, name: str = "t.tbl") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Multi-prefix declaration & lookup
# ---------------------------------------------------------------------------

def test_multiprefix_declaration(tmp_path):
    """@ctrl_prefix accepts a space-separated byte list."""
    tbl = Table(_write_table(tmp_path, (
        "@ctrl_prefix F7 F8 F9 FA FB FC FD FE\n"
        "41=A\n"
    )))
    assert tbl.ctrl_prefixes == [0xF7, 0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE]
    # Backward-compat property returns the first prefix.
    assert tbl.ctrl_prefix == 0xF7


def test_prefix_default_length(tmp_path):
    """`@ctrl PP=N` sets the prefix-default length when PP is a declared prefix."""
    tbl = Table(_write_table(tmp_path, (
        "@ctrl_prefix F7 F8 FC FD\n"
        "@ctrl F7=2\n"   # F7 + 1 byte
        "@ctrl F8=2\n"   # F8 + 1 byte
        "@ctrl FC=3\n"   # FC + 2 bytes (default)
        "@ctrl FD=1\n"   # FD standalone
    )))
    assert tbl.ctrl_lookup(0xF7) == 2
    assert tbl.ctrl_lookup(0xF8) == 2
    assert tbl.ctrl_lookup(0xFC) == 3
    assert tbl.ctrl_lookup(0xFD) == 1
    # Undeclared prefix returns None.
    assert tbl.ctrl_lookup(0xAA) is None


def test_dotted_per_prefix_cmd_override(tmp_path):
    """`@ctrl PP.XX=N` overrides the length for that specific cmd byte."""
    tbl = Table(_write_table(tmp_path, (
        "@ctrl_prefix FC\n"
        "@ctrl FC=3\n"        # FC default = 3 bytes (FC + 2 more)
        "@ctrl FC.02=4\n"     # but FC 02 xx yy = 4 bytes
        "@ctrl FC.05=5\n"     # and FC 05 xx yy zz = 5 bytes
    )))
    # Default fires for cmds without an override.
    assert tbl.ctrl_lookup(0xFC, 0x00) == 3
    assert tbl.ctrl_lookup(0xFC, 0x01) == 3
    # Overrides fire for explicit cmd bytes.
    assert tbl.ctrl_lookup(0xFC, 0x02) == 4
    assert tbl.ctrl_lookup(0xFC, 0x05) == 5
    # `cmd=None` returns the prefix default itself.
    assert tbl.ctrl_lookup(0xFC) == 3


def test_ctrl_table_snapshot(tmp_path):
    """`ctrl_table()` exposes per-prefix (default, cmds) tuples."""
    tbl = Table(_write_table(tmp_path, (
        "@ctrl_prefix F7 FC\n"
        "@ctrl F7=2\n"
        "@ctrl FC=3\n"
        "@ctrl FC.02=4\n"
    )))
    snap = tbl.ctrl_table()
    assert snap[0xF7] == (2, {})
    assert snap[0xFC] == (3, {0x02: 4})


# ---------------------------------------------------------------------------
# Backward compatibility — single-prefix tables behave unchanged
# ---------------------------------------------------------------------------

def test_single_prefix_legacy_form_works(tmp_path):
    """`@ctrl_prefix FF` + `@ctrl C0=4` is the original syntax. Must still work."""
    tbl = Table(_write_table(tmp_path, (
        "@ctrl_prefix FF\n"
        "@ctrl C0=4\n"
        "41=A\n"
        "00=[end]\n"
    )))
    assert tbl.ctrl_prefix == 0xFF
    assert tbl.ctrl_prefixes == [0xFF]
    # cmd lookup: C0 has explicit length, default is the unset prefix default 3.
    assert tbl.ctrl_lookup(0xFF, 0xC0) == 4
    assert tbl.ctrl_lookup(0xFF, 0x99) == 3
    # Flat ctrl_lengths view preserved.
    assert tbl.ctrl_lengths == {0xC0: 4}


def test_legacy_wildcard_form_still_parses(tmp_path):
    """`@ctrl 7F**=3` (trailing `**` legacy wildcard) maps to single-byte cmd entry."""
    tbl = Table(_write_table(tmp_path, (
        "@ctrl_prefix FF\n"
        "@ctrl 7F**=3\n"
    )))
    # Wildcard form == bare-byte form for storage (the `**` is informational).
    assert tbl.ctrl_lookup(0xFF, 0x7F) == 3
    assert tbl.ctrl_lengths == {0x7F: 3}


def test_no_ctrl_directives_at_all(tmp_path):
    """Table with no @ctrl_prefix and no @ctrl entries: empty prefix list,
    `ctrl_prefix` property falls back to $FF for backward compatibility."""
    tbl = Table(_write_table(tmp_path, "41=A\n42=B\n"))
    assert tbl.ctrl_prefixes == []
    assert tbl.ctrl_prefix == 0xFF
    assert tbl.ctrl_lengths == {}


def test_ctrl_without_prefix_declaration_falls_back_to_FF(tmp_path):
    """`@ctrl C0=4` with no `@ctrl_prefix` declaration: treat as cmd under $FF
    so historical tables that omitted the prefix line still work."""
    tbl = Table(_write_table(tmp_path, "@ctrl C0=4\n41=A\n"))
    assert tbl.ctrl_prefixes == [0xFF]
    assert tbl.ctrl_lookup(0xFF, 0xC0) == 4


# ---------------------------------------------------------------------------
# Runtime walk: find_entry_end + interpret_binary_data with multi-prefix
# ---------------------------------------------------------------------------

def test_find_entry_end_multiprefix_walk(tmp_path):
    """find_entry_end consumes the correct byte count for each prefix.

    Stream: A B [F7 99] [F8 00] [FC 02 11 22] [FD] [FF]
            41 42  F7 99  F8 00  FC 02 11 22   FD    FF (terminator)
    Note F8's param byte is 00 (==hypothetical \\$00 terminator) — but rbshura
    uses FF as terminator so this is fine. The point is the walker must
    consume the right number of bytes per opcode.
    """
    tbl = Table(_write_table(tmp_path, (
        "@ctrl_prefix F7 F8 FC FD\n"
        "@ctrl F7=2\n"
        "@ctrl F8=2\n"
        "@ctrl FC=3\n"
        "@ctrl FC.02=4\n"   # FC 02 xx yy = 4 bytes
        "@ctrl FD=1\n"
        "41=A\n42=B\n"
    )))
    data = bytes([0x41, 0x42, 0xF7, 0x99, 0xF8, 0x00,
                  0xFC, 0x02, 0x11, 0x22, 0xFD, 0xFF])
    end = tbl.find_entry_end(data, 0, terminator=0xFF)
    # End points PAST the FF terminator (index 12 for a 12-byte string).
    assert end == 12


def test_find_entry_end_FF_as_standalone_and_terminator(tmp_path):
    """For rbshura: FF is BOTH a 1-byte standalone STOP opcode AND the
    string terminator. find_entry_end must return on the FF byte even
    though FF is in the @ctrl_prefix set."""
    tbl = Table(_write_table(tmp_path, (
        "@ctrl_prefix F7 FF\n"
        "@ctrl F7=2\n"
        "@ctrl FF=1\n"
        "41=A\n"
    )))
    # 4 bytes: A [F7 99] [FF]
    data = bytes([0x41, 0xF7, 0x99, 0xFF])
    end = tbl.find_entry_end(data, 0, terminator=0xFF)
    assert end == 4
    raw = data[:end]
    assert raw == b"\x41\xF7\x99\xFF"


def test_interpret_binary_data_brackets_each_ctrl(tmp_path):
    """Decode emits one `[HHHH]` hex span per ctrl, regardless of which
    prefix byte triggered it. Plain chars decode normally."""
    tbl = Table(_write_table(tmp_path, (
        "@ctrl_prefix F7 F8 FC\n"
        "@ctrl F7=2\n"
        "@ctrl F8=2\n"
        "@ctrl FC=3\n"
        "@ctrl FC.02=4\n"
        "41=A\n"
        "42=B\n"
    )))
    data = [0x41, 0xF7, 0x99, 0x42, 0xF8, 0xAA, 0xFC, 0x02, 0x11, 0x22]
    text = tbl.interpret_binary_data(data, max_bytes=3)
    # A [F799] B [F8AA] [FC021122]
    assert "[F7][99]" in text
    assert "[F8][AA]" in text
    assert "[FC][02][11][22]" in text
    assert text.startswith("A") and "B" in text


def test_find_entry_end_rejects_multibyte_window_ending_in_any_prefix(tmp_path):
    """A multi-byte char match must not consume a window whose last byte
    is one of the declared ctrl prefixes — that byte belongs to the next
    ctrl sequence, not the current char."""
    tbl = Table(_write_table(tmp_path, (
        "@ctrl_prefix F7 FF\n"
        "@ctrl F7=2\n"
        "@ctrl FF=1\n"
        # Multi-byte char whose 2-byte encoding ends in F7 (the prefix) —
        # must NOT match here, even though the value is in the table.
        "12F7=Z\n"
        "12=Q\n"
    )))
    # Bytes: 12 F7 99 FF. If 12F7 were folded into 'Z', we'd skip past F7
    # and miss the F7-ctrl. Correct walk: 12 → 'Q', then F7 99 → ctrl, then FF → end.
    data = bytes([0x12, 0xF7, 0x99, 0xFF])
    end = tbl.find_entry_end(data, 0, terminator=0xFF)
    assert end == 4

"""Tests for `--only NAME:BLOCK[:WIN]` parsing and filter-aware script builds."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from retrotool.build import (
    BuildSpec, IndexRange, ScriptFilter, ScriptTarget, Section, SectionKind,
    build, parse_only_args, parse_only_token,
)
from retrotool.build.handlers import HandlerError
from tests.build.conftest import _make_lorom


_ASCII_TBL = (
    "\n".join(f"{ord(c):02X}={c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ abcd")
    + "\n"
)


# ---------- parser ----------------------------------------------------------

def test_parse_only_token_section_only():
    assert parse_only_token("dialog-1") == ("dialog-1", None, None)
    assert parse_only_token("asar") == ("asar", None, None)


def test_parse_only_token_block_single():
    sec, b, w = parse_only_token("dialog-1:42")
    assert sec == "dialog-1"
    assert b == IndexRange(42, 42)
    assert w is None


def test_parse_only_token_block_range():
    sec, b, w = parse_only_token("dialog-1:42-50")
    assert (sec, b, w) == ("dialog-1", IndexRange(42, 50), None)


def test_parse_only_token_window_single():
    sec, b, w = parse_only_token("dialog-1:42:0")
    assert (sec, b, w) == ("dialog-1", IndexRange(42, 42), IndexRange(0, 0))


def test_parse_only_token_window_range():
    sec, b, w = parse_only_token("dialog-1:42:0-3")
    assert b == IndexRange(42, 42)
    assert w == IndexRange(0, 3)


def test_parse_only_token_block_range_with_window():
    sec, b, w = parse_only_token("dialog-1:42-50:0-3")
    assert b == IndexRange(42, 50)
    assert w == IndexRange(0, 3)


def test_parse_only_token_open_lower_bound():
    _, b, _ = parse_only_token("dialog-1:-50")
    assert b == IndexRange(0, 50)


def test_parse_only_token_open_upper_bound():
    _, b, _ = parse_only_token("dialog-1:42-")
    assert b.lo == 42 and b.hi >= 0x7FFFFFF0


def test_parse_only_token_keeps_section_id_with_colon_when_no_range():
    # `project.toml:sections[7]` is a legal section locator — colon must not
    # be misread as a block-spec separator when the suffix isn't a range.
    sec, b, w = parse_only_token("project.toml:sections[7]")
    assert sec == "project.toml:sections[7]"
    assert b is None and w is None


def test_parse_only_token_rejects_inverted_range():
    with pytest.raises(ValueError):
        parse_only_token("dialog-1:5-2")


def test_parse_only_token_bare_dash_treated_as_section_text():
    # `dialog-1:-` has a non-range suffix (bare `-`), so the whole thing
    # is taken as the section ID — won't match anything in the build
    # pipeline, but parsing must not crash.
    sec, b, w = parse_only_token("dialog-1:-")
    assert sec == "dialog-1:-"
    assert b is None and w is None


# ---------- ScriptFilter ----------------------------------------------------

def test_filter_block_allowed_when_no_rule():
    sf = ScriptFilter()
    assert sf.block_allowed({"dialog-1"}, 0) is True


def test_filter_block_allowed_in_range():
    sf = ScriptFilter()
    sf.add(ScriptTarget("dialog-1", IndexRange(40, 45), None))
    assert sf.block_allowed({"dialog-1"}, 42) is True
    assert sf.block_allowed({"dialog-1"}, 39) is False
    assert sf.block_allowed({"dialog-1"}, 46) is False


def test_filter_window_only_active_when_block_matches():
    sf = ScriptFilter()
    sf.add(ScriptTarget("dialog-1", IndexRange(42, 42), IndexRange(0, 0)))
    # Block 42, window 0 — allowed.
    assert sf.window_allowed({"dialog-1"}, 42, 0) is True
    assert sf.window_allowed({"dialog-1"}, 42, 1) is False
    # Block 41 doesn't match — window filter doesn't apply (no rule active).
    assert sf.window_allowed({"dialog-1"}, 41, 5) is True


def test_filter_section_id_lowercased():
    sf = ScriptFilter()
    sf.add(ScriptTarget("Dialog-1", IndexRange(0, 0), None))
    assert sf.block_allowed({"dialog-1"}, 0) is True


def test_parse_only_args_splits_section_and_filter():
    only_set, sf = parse_only_args({"dialog-1:42", "asar"})
    assert "asar" in only_set
    assert "dialog-1" in only_set
    assert sf.block_allowed({"dialog-1"}, 42) is True
    assert sf.block_allowed({"dialog-1"}, 41) is False


# ---------- handler integration --------------------------------------------

def _setup_overflow_section(tmp_path):
    """Build a tiny overflow-mode script project with three entries.

    Source ROM has three short entries followed by `0x00` terminators at
    PCs $100, $108, $110. Pointer table at $200 lists those PCs.
    """
    (tmp_path / "t.tbl").write_text(_ASCII_TBL, encoding="utf-8")
    # Each entry: 3 chars + terminator, fits in 4 bytes per slot.
    # Source bytes at original PCs (will be preserved when filtered out).
    plant = {
        0x100: b"AAA\x00",
        0x108: b"BBB\x00",
        0x110: b"CCC\x00",
        # pointer table at 0x200 (LoROM bank 0 → 16-bit within-bank ptrs).
        0x200: b"\x00\x81\x08\x81\x10\x81",  # 0x8100, 0x8108, 0x8110
    }
    rom_path = _make_lorom(tmp_path, plant=plant)
    # Replacement script: re-encode every entry with new content. Header bank
    # uses hex letters ($C000) so encode_script_file doesn't mistake it for
    # a decimal `sub_table_filter` discriminator (the regex `\$(\d+):` only
    # matches all-digit banks; letters bypass the filter).
    (tmp_path / "s.txt").write_text(
        "<<$C000:0[$100]>>\nDDD\n"
        "<<$C000:1[$108]>>\nEEE\n"
        "<<$C000:2[$110]>>\nFFF\n",
        encoding="utf-8",
    )
    return rom_path


def test_overflow_filter_only_one_block_preserves_others(tmp_path):
    rom_path = _setup_overflow_section(tmp_path)
    spec = BuildSpec(
        sections=[Section(
            kind=SectionKind.SCRIPT,
            files=[PurePosixPath("s.txt")],
            table=PurePosixPath("t.tbl"),
            pointer_table=0x200,
            pointer_size=2,
            count=3,
            placement={"mode": "overflow"},
            attrs={"name": "dialog"},
            source="inline:dialog",
        )],
        freespace=[(0x10000, 0x20000)],
    )
    sf = ScriptFilter()
    sf.add(ScriptTarget("dialog", IndexRange(1, 1), None))
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path,
          script_filter=sf)
    body = out.read_bytes()
    # Block 0 untouched.
    assert body[0x100:0x104] == b"AAA\x00"
    # Block 1 rewritten (3 chars fit into the 3-byte slot).
    assert body[0x108:0x10B] == b"EEE"
    # Block 2 untouched.
    assert body[0x110:0x114] == b"CCC\x00"


def test_overflow_filter_block_range(tmp_path):
    rom_path = _setup_overflow_section(tmp_path)
    spec = BuildSpec(
        sections=[Section(
            kind=SectionKind.SCRIPT,
            files=[PurePosixPath("s.txt")],
            table=PurePosixPath("t.tbl"),
            pointer_table=0x200,
            pointer_size=2,
            count=3,
            placement={"mode": "overflow"},
            attrs={"name": "dialog"},
            source="inline:dialog",
        )],
        freespace=[(0x10000, 0x20000)],
    )
    sf = ScriptFilter()
    sf.add(ScriptTarget("dialog", IndexRange(0, 1), None))
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path,
          script_filter=sf)
    body = out.read_bytes()
    assert body[0x100:0x103] == b"DDD"   # block 0 rewritten
    assert body[0x108:0x10B] == b"EEE"   # block 1 rewritten
    assert body[0x110:0x114] == b"CCC\x00"  # block 2 preserved


def test_relocate_mode_rejects_block_filter(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "t.tbl").write_text(_ASCII_TBL, encoding="utf-8")
    (tmp_path / "s.txt").write_text(
        "<<$C000:0[$100]>>\nABC\n<<$C000:1[$110]>>\nDE\n",
        encoding="utf-8",
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.SCRIPT,
        files=[PurePosixPath("s.txt")],
        table=PurePosixPath("t.tbl"),
        pointer_table=0x600,
        pointer_size=2,
        count=2,
        placement={"mode": "relocate"},
        attrs={"name": "dialog"},
        source="inline:dialog",
    )])
    sf = ScriptFilter()
    sf.add(ScriptTarget("dialog", IndexRange(0, 0), None))
    with pytest.raises(HandlerError, match="overflow"):
        build(spec, source_root=tmp_path,
              out_path=tmp_path / "out.sfc", original_rom=rom_path,
              script_filter=sf)


def test_section_only_filter_works_in_relocate_mode(tmp_path):
    """Section-level filter (no `:BLOCK`) is just `--only NAME` — must not
    trip the relocate-mode block-filter rejection."""
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "t.tbl").write_text(_ASCII_TBL, encoding="utf-8")
    (tmp_path / "s.txt").write_text(
        "<<$C000:0[$100]>>\nABC\n<<$C000:1[$110]>>\nDE\n",
        encoding="utf-8",
    )
    spec = BuildSpec(sections=[Section(
        kind=SectionKind.SCRIPT,
        files=[PurePosixPath("s.txt")],
        table=PurePosixPath("t.tbl"),
        pointer_table=0x600,
        pointer_size=2,
        count=2,
        placement={"mode": "relocate"},
        attrs={"name": "dialog"},
        source="inline:dialog",
    )])
    only_set, sf = parse_only_args({"dialog"})
    # No block-level rule: filter is empty.
    assert sf.is_empty()
    build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
          original_rom=rom_path, only=only_set,
          script_filter=sf if not sf.is_empty() else None)

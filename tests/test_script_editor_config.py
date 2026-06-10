"""Tests for the script editor's project configuration + file model.

Covers retrotool.script.editor_config (defaults, [editor] overrides, hex
parsing, per-file render overrides) and the encoding-preserving Scenario
round-trip plus the table-driven body parsing in script_editor (none of
which need pywebview or pillow).
"""
from pathlib import Path

import pytest

from retrotool.script.editor_config import (
    ControlCodes,
    load_editor_config,
)
from retrotool.script.script_editor import (
    Scenario,
    body_byte_count,
    body_overflow,
    extract_palette_id,
    load_char_table,
)


# ---------------------------------------------------------------------------
# load_editor_config
# ---------------------------------------------------------------------------

def test_defaults_without_project_toml(tmp_path: Path):
    cfg = load_editor_config(tmp_path)
    assert cfg.root == tmp_path.resolve()
    assert cfg.lang == "en"
    assert cfg.data_dir == tmp_path.resolve()   # no data/en → project root
    assert cfg.reference_lang is None
    assert cfg.table is None
    assert cfg.file_patterns == ["*.txt"]
    assert cfg.cols_per_line == 24
    assert not cfg.preview.enabled
    # Stock control codes
    assert cfg.control.newline == 0xFD
    assert cfg.control.page_break == 0xFE
    assert cfg.control.terminator == 0xFF
    assert cfg.control.opcode_lengths[0xF7] == 2
    assert cfg.control.subcode_lengths[0xFC][0x02] == 4


def test_follows_build_lang_and_data_dirs(tmp_path: Path):
    (tmp_path / "scripts" / "br_pt").mkdir(parents=True)
    (tmp_path / "scripts" / "jp").mkdir(parents=True)
    (tmp_path / "tables").mkdir()
    (tmp_path / "tables" / "game_br_pt.tbl").write_text("00=a\n")
    (tmp_path / "tables" / "game_jp.tbl").write_text("00=あ\n")
    (tmp_path / "project.toml").write_text(
        'build_lang = "br_pt"\n'
        'br_pt_data_dir = "scripts/br_pt"\n'
        'jp_data_dir = "scripts/jp"\n'
        '[rom]\nname = "mygame"\n'
    )
    cfg = load_editor_config(tmp_path)
    assert cfg.lang == "br_pt"
    assert cfg.project_name == "mygame"
    assert cfg.data_dir == (tmp_path / "scripts" / "br_pt").resolve()
    assert cfg.reference_lang == "jp"
    assert cfg.reference_dir == (tmp_path / "scripts" / "jp").resolve()
    # Table discovery via tables/*_<lang>.tbl
    assert cfg.table.name == "game_br_pt.tbl"
    assert cfg.reference_table.name == "game_jp.tbl"
    # Default ROM path = [rom].name under default output_dir
    assert cfg.preview.rom == (tmp_path / "out" / "mygame.sfc").resolve()


def test_editor_overrides(tmp_path: Path):
    (tmp_path / "text").mkdir()
    (tmp_path / "project.toml").write_text(
        '[editor]\n'
        'lang = "fr"\n'
        'data_dir = "text"\n'
        'table = "tbl/game_${lang}.tbl"\n'
        'file_patterns = ["dialog_*.txt", "menus.txt"]\n'
        'cols_per_line = 28\n'
        '[editor.control_codes]\n'
        'newline = "F0"\n'
        'page_break = "$F1"\n'
        'terminator = "0x00"\n'
        'palette_code = ""\n'
        'opcodes = { F2 = 3 }\n'
        '[editor.control_codes.subcodes.F3]\n'
        'default = 2\n'
        '"0A" = 5\n'
        '[editor.preview]\n'
        'font = "fonts/font.bin"\n'
        'glyph_width = 16\n'
        'palette_table = "0x012345"\n'
        '[editor.files."intro"]\n'
        'cols_per_line = 32\n'
        'kanji_escape = true\n'
        'forced_wrap = true\n'
    )
    cfg = load_editor_config(tmp_path)
    assert cfg.lang == "fr"
    assert cfg.data_dir == (tmp_path / "text").resolve()
    assert cfg.table == (tmp_path / "tbl" / "game_fr.tbl").resolve()
    assert cfg.file_patterns == ["dialog_*.txt", "menus.txt"]
    assert cfg.cols_per_line == 28
    cc = cfg.control
    assert (cc.newline, cc.page_break, cc.terminator) == (0xF0, 0xF1, 0x00)
    assert cc.palette_code is None          # "" disables
    assert cc.opcode_lengths == {0xF2: 3}
    assert cc.subcode_lengths == {0xF3: {0x0A: 5, None: 2}}
    assert cfg.preview.glyph_width == 16
    assert cfg.preview.palette_table == 0x012345
    ov = cfg.files["intro"]
    assert ov.cols_per_line == 32 and ov.kanji_escape and ov.forced_wrap


def test_bad_hex_raises(tmp_path: Path):
    (tmp_path / "project.toml").write_text(
        '[editor.control_codes]\nnewline = "XYZ"\n'
    )
    with pytest.raises(ValueError, match="newline"):
        load_editor_config(tmp_path)


def test_reserved_bytes():
    cc = ControlCodes()
    reserved = cc.reserved_bytes()
    assert {0xF7, 0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF} <= reserved
    assert 0x41 not in reserved


# ---------------------------------------------------------------------------
# Scenario file model — encoding-preserving round-trip
# ---------------------------------------------------------------------------

SCRIPT_TEXT = (
    "<<$00C000:0[$1]>>\nHello[FD]world[FF]\n"
    "<<$00C010:1[$2]>>\nSecond entry[FF]\n"
)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_scenario_roundtrip_preserves_encoding(tmp_path: Path, encoding: str):
    p = tmp_path / "scenario_01.txt"
    p.write_text(SCRIPT_TEXT, encoding=encoding)
    sc = Scenario(p)
    assert sc.encoding == encoding
    assert [e["idx"] for e in sc.entries] == [0, 1]
    assert sc.entries[0]["body"] == "Hello[FD]world[FF]"

    sc.save_entry(0, "Bonjour[FD]monde[FF]")
    sc2 = Scenario(p)
    assert sc2.encoding == encoding
    assert sc2.entries[0]["body"] == "Bonjour[FD]monde[FF]"
    assert sc2.entries[1]["body"] == "Second entry[FF]"


def test_scenario_fixed_record_headers(tmp_path: Path):
    p = tmp_path / "char_names.txt"
    p.write_text("<<$1F00:0.hero>>\nRICK[FF]\n", encoding="utf-8")
    sc = Scenario(p)
    assert sc.entries[0]["header"] == "<<$1F00:0.hero>>"
    assert sc.entries[0]["body"] == "RICK[FF]"


# ---------------------------------------------------------------------------
# Body parsing with configurable control codes
# ---------------------------------------------------------------------------

def _table():
    return {c: 0x10 + i for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")} | {" ": 0x00}


def test_byte_count_counts_brackets_and_chars():
    cfg = {"control": ControlCodes()}
    # 5 chars + FD + 5 chars + FF = 12 bytes; soft newline costs 0
    assert body_byte_count("hello[FD]\nworld[FF]", _table(), cfg) == 12


def test_window_markers_ignored():
    cfg = {"control": ControlCodes()}
    body = "<<<window 0: $0F8000-$0FFFFF>>>\nhello[FF]"
    assert body_byte_count(body, _table(), cfg) == 6


def test_overflow_uses_explicit_lines():
    cfg = {"control": ControlCodes(), "cols_per_line": 4}
    # One explicit 5-char line → overflow; newline byte splits lines.
    r = body_overflow("hello[FD]ok[FF]", _table(), cfg)
    assert r["limit"] == 4
    assert [ln["line"] for ln in r["lines"]] == [1]
    # forced_wrap files never report overflow
    cfg["forced_wrap"] = True
    assert body_overflow("hello[FF]", _table(), cfg)["lines"] == []


def test_custom_control_codes():
    cc = ControlCodes(newline=0x01, page_break=0x02, terminator=0x03,
                      palette_code=None, opcode_lengths={0x04: 2},
                      subcode_lengths={})
    cfg = {"control": cc, "cols_per_line": 3}
    # 0x01 splits the line; 0x04's param byte is skipped (not a glyph), so
    # line 1 = "ab" (2 units, ok) and line 2 = "cdef" (4 units, over).
    r = body_overflow("ab[01]cd[04][7F]ef[03]", _table(), cfg)
    assert [(ln["line"], ln["units"]) for ln in r["lines"]] == [(2, 4)]
    # Anything after the terminator (0x03) is never measured.
    r2 = body_overflow("ab[03]waytoolongline", _table(), cfg)
    assert r2["lines"] == []


def test_extract_palette_id_all_bracket_forms():
    cc = ControlCodes()
    assert extract_palette_id("[F904]hi", cc) == 0x04
    assert extract_palette_id("[F9][05]hi", cc) == 0x05
    assert extract_palette_id("[F9:06]hi", cc) == 0x06
    assert extract_palette_id("plain text", cc) is None
    assert extract_palette_id("[F904]", ControlCodes(palette_code=None)) is None


def test_load_char_table_skips_control_bytes(tmp_path: Path):
    tbl = tmp_path / "t.tbl"
    tbl.write_text(
        "@something directive\n"
        "# comment\n"
        "41=A\n"
        "FD=\\n   ; control byte — must be skipped\n"
        "F7AB=x  ; multi-byte — skipped\n"
        "42=B\n",
        encoding="utf-8",
    )
    m = load_char_table(tbl, ControlCodes().reserved_bytes())
    assert m["A"] == 0x41 and m["B"] == 0x42
    assert "\\n" not in m
    assert m[" "] == 0x00   # space convention

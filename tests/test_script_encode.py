"""Unit tests for retrotool.script.encode (LM3-parity script encoder)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from retrotool.script.encode import (
    encode_text,
    word_wrap_text,
    entry_in_range,
    encode_script_file,
    ScriptFixup,
)
from retrotool.script.table import Table


def _write_table(tmp_path: Path, body: str, name: str = "t.tbl") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_encode_basic_chars(tmp_path):
    tbl_path = _write_table(tmp_path, "41=A\n42=B\n43=C\n00=[end]\n")
    tbl = Table(tbl_path)
    encoded, fixups, labels = encode_text("ABC", tbl)
    assert encoded == b"\x41\x42\x43"
    assert fixups == []
    assert labels == {}


def test_encode_hex_escape(tmp_path):
    tbl = Table(_write_table(tmp_path, "41=A\n"))
    encoded, _, _ = encode_text("A[FF7F01]", tbl)
    assert encoded == b"\x41\xFF\x7F\x01"


def test_encode_bytecode_brace(tmp_path):
    tbl = Table(_write_table(tmp_path, "41=A\n"))
    encoded, _, _ = encode_text("A{1B}", tbl)
    assert encoded == b"\x41\x1B"


def test_encode_ffc0_fixup_and_label(tmp_path):
    tbl = Table(_write_table(tmp_path, "41=A\n42=B\n"))
    encoded, fixups, labels = encode_text("A[FFC0@5:foo]B[label:bar]", tbl)
    # A FF C0 FF FF FF B   →  bytes 0=A, 1-2=FFC0, 3-5=FFFFFF placeholder, 6=B
    assert encoded == b"\x41\xFF\xC0\xFF\xFF\xFF\x42"
    assert fixups == [ScriptFixup(offset=3, entry_idx=5, label="foo")]
    # label is zero-width and recorded after the B (offset 7).
    assert labels == {"bar": 7}


def test_encode_fallback_table(tmp_path):
    primary = Table(_write_table(tmp_path, "41=A\n", "p.tbl"))
    fb = Table(_write_table(tmp_path, "5F=(jp)\n", "f.tbl"))
    encoded, _, _ = encode_text("A(jp)A", primary, fallback_table=fb)
    assert encoded == b"\x41\x5F\x41"


def test_encode_multibyte_be(tmp_path):
    tbl = Table(_write_table(tmp_path, "FF7F01=[pause]\n"))
    encoded, _, _ = encode_text("[pause]", tbl)
    assert encoded == b"\xFF\x7F\x01"


def test_word_wrap_basic():
    out, trunc, n = word_wrap_text("hello world this is text", 11, 3)
    # "hello world" = 11 (auto-wrap, no [nl]); then "this is" + "text"
    assert "[nl]" in out or out.startswith("hello world")
    assert not trunc


def test_word_wrap_truncate_keeps_hex_codes():
    # Hex codes [HHHH] and {HH} bytecodes survive truncation;
    # named codes like [end] do not (matches LM3 behavior).
    out, trunc, _ = word_wrap_text("aaa bbb ccc[FF7F][end]", 3, 1)
    assert trunc
    assert "[FF7F]" in out
    assert "[end]" not in out


def test_entry_in_range():
    assert entry_in_range(5, "0-10")
    assert entry_in_range(7, "0,5,7-9")
    assert not entry_in_range(100, "0-10")
    assert entry_in_range(42, None)


def test_encode_script_file_basic(tmp_path):
    tbl_path = _write_table(tmp_path, "41=A\n42=B\n43=C\n")
    script = tmp_path / "s.txt"
    script.write_text(textwrap.dedent("""
        <<$11E3:0[$1234]>>
        ABC
        <<$11E3:1[$1240]>>
        BC
        <<$11E3:2[$1245]>>
        [end]
    """).lstrip(), encoding="utf-8")
    entries = encode_script_file(script, tbl_path)
    assert [e[0] for e in entries] == [b"\x41\x42\x43", b"\x42\x43", b"\x00"]
    assert [e[1] for e in entries] == [1234, 1240, 1245]


def test_encode_script_file_word_wrap(tmp_path):
    tbl_path = _write_table(tmp_path, "41=A\n42=B\n43=C\n44=D\n45=E\n0A=[nl]\n")
    script = tmp_path / "s.txt"
    script.write_text(
        "<<$11E3:0[$1000]>>\nAA BB CC DD\n",
        encoding="utf-8",
    )
    # line_width=3, max_lines=4: "AA " uses col 3, "BB" overflows → [nl].
    entries = encode_script_file(
        script, tbl_path,
        word_wrap={"line_width": 3, "max_lines": 4, "entries": "0-100"},
    )
    encoded = entries[0][0]
    assert b"\x0A" in encoded
    assert encoded.startswith(b"\x41\x41")


def test_encode_generalized_opcode_entry_ref(tmp_path):
    tbl = Table(_write_table(tmp_path, "41=A\n"))
    # [FFF7@3] — different opcode than FFC0, same mechanics.
    encoded, fixups, _ = encode_text("A[FFF7@3]", tbl)
    assert encoded == b"\x41\xFF\xF7\xFF\xFF\xFF"
    assert fixups == [ScriptFixup(offset=3, entry_idx=3, label=None)]


def test_encode_global_label_ref(tmp_path):
    tbl = Table(_write_table(tmp_path, "41=A\n"))
    encoded, fixups, _ = encode_text("A[FFC0@@dte_start]", tbl)
    assert encoded == b"\x41\xFF\xC0\xFF\xFF\xFF"
    assert fixups == [ScriptFixup(offset=3, global_label="dte_start")]


def test_interpret_binary_data_respects_ctrl_lengths(tmp_path):
    """Ctrl spans declared by `@ctrl` emit a single hex escape covering
    the full payload. Before this fix, interpret_binary_data split
    multi-byte ctrls across a 3-byte hex escape + literal decodes,
    breaking round-trip via encode_text (LM3 FF 9C=5 case)."""
    tbl = Table(_write_table(tmp_path,
        "@ctrl_prefix FF\n@ctrl 9C=5\n0E=X\nBA=Y\n00=[end]\n"))
    decoded = tbl.interpret_binary_data([0xFF, 0x9C, 0x52, 0x0E, 0x00, 0xBA])
    # All 5 bytes of the ctrl carried together; then Y for 0xBA.
    assert "[FF][9C][52][0E][00]" in decoded
    assert decoded.endswith("Y")
    # Round-trip via encode_text reproduces the original bytes.
    enc, _, _ = encode_text(decoded, tbl)
    assert enc == b"\xFF\x9C\x52\x0E\x00\xBA"


def test_table_ctrl_type_parsed(tmp_path):
    tbl_text = "@ctrl C0=5 type=redirect\n@ctrl F7=3 type=redirect\n@ctrl E0**=2\n41=A\n"
    tbl = Table(_write_table(tmp_path, tbl_text))
    assert tbl.ctrl_lengths[0xC0] == 5
    assert tbl.ctrl_types[0xC0] == "redirect"
    assert tbl.ctrl_types[0xF7] == "redirect"
    # untagged @ctrl absent from types
    assert 0xE000 not in tbl.ctrl_types

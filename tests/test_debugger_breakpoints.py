"""Tests for retrotool.debugger.breakpoints — symfile → .bp converter."""
from __future__ import annotations

from pathlib import Path

import pytest

from retrotool.debugger.breakpoints import (
    Breakpoint,
    BreakpointError,
    make_mesen_breakpoints,
    parse_symfile,
    read_breakpoints,
    to_mesen_calls,
)


_SYM_FIXTURE = """\
al 008000 .main_loop         ;x
al 7E1000 .player_hp         ;w
al 008100 .sound_driver      ;x:smp
al 002100 .inidisp           ;rw:cpu
al 00C000 .unannotated_label
"""


def _write_sym(tmp_path: Path) -> Path:
    p = tmp_path / "game.sym"
    p.write_text(_SYM_FIXTURE)
    return p


def test_parse_symfile_picks_annotated_labels(tmp_path):
    bps = parse_symfile(_write_sym(tmp_path))
    assert [b.label for b in bps] == ["main_loop", "player_hp", "sound_driver", "inidisp"]


def test_parse_symfile_resolves_rwx_and_mem(tmp_path):
    bps = parse_symfile(_write_sym(tmp_path))
    assert (bps[0].rwx, bps[0].mem) == ("x", "cpu")
    assert (bps[1].rwx, bps[1].mem) == ("w", "cpu")
    assert (bps[2].rwx, bps[2].mem) == ("x", "smp")
    assert (bps[3].rwx, bps[3].mem) == ("rw", "cpu")


def test_parse_symfile_addresses_hex(tmp_path):
    bps = parse_symfile(_write_sym(tmp_path))
    assert bps[0].address == 0x008000
    assert bps[1].address == 0x7E1000


def test_make_mesen_breakpoints_default_path(tmp_path):
    sym = _write_sym(tmp_path)
    out = make_mesen_breakpoints(sym)
    assert out == sym.with_suffix(".sym.bp")
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 4
    assert lines[0] == "-b 008000:x:cpu"
    assert lines[2] == "-b 008100:x:smp"


def test_make_mesen_breakpoints_explicit_out(tmp_path):
    sym = _write_sym(tmp_path)
    out = tmp_path / "nested" / "custom.bp"
    ret = make_mesen_breakpoints(sym, out_bp=out)
    assert ret == out
    assert out.exists()


def test_round_trip_read_breakpoints(tmp_path):
    sym = _write_sym(tmp_path)
    bp_file = make_mesen_breakpoints(sym)
    bps = read_breakpoints(bp_file)
    assert [(b.address, b.rwx, b.mem) for b in bps] == [
        (0x008000, "x", "cpu"),
        (0x7E1000, "w", "cpu"),
        (0x008100, "x", "smp"),
        (0x002100, "rw", "cpu"),
    ]


def test_to_mesen_calls_expands_rw(tmp_path):
    bps = parse_symfile(_write_sym(tmp_path))
    calls = to_mesen_calls(bps)
    # main_loop(x) + player_hp(w) + sound_driver(x) + inidisp(r,w) = 5
    assert len(calls) == 5
    assert calls[0] == (0x008000, "SnesMemory", "exec")
    assert calls[2] == (0x008100, "Spc700Memory", "exec")
    assert calls[3] == (0x002100, "SnesMemory", "read")
    assert calls[4] == (0x002100, "SnesMemory", "write")


def test_invalid_rwx_raises(tmp_path):
    p = tmp_path / "bad.sym"
    p.write_text("al 008000 .bogus ;q\n")
    with pytest.raises(BreakpointError, match="invalid rwx"):
        parse_symfile(p)


def test_invalid_mem_raises(tmp_path):
    p = tmp_path / "bad.sym"
    p.write_text("al 008000 .bogus ;x:nowhere\n")
    with pytest.raises(BreakpointError, match="unknown memory"):
        parse_symfile(p)


def test_parse_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_symfile(tmp_path / "nope.sym")


def test_empty_input_writes_empty_file(tmp_path):
    p = tmp_path / "empty.sym"
    p.write_text("al 00C000 .no_hint\n")
    out = make_mesen_breakpoints(p)
    assert out.read_text() == ""


def test_read_breakpoints_rejects_garbage(tmp_path):
    p = tmp_path / "bad.bp"
    p.write_text("not a breakpoint\n")
    with pytest.raises(BreakpointError):
        read_breakpoints(p)


def test_breakpoint_token_format():
    bp = Breakpoint(address=0x80FF, label="x", rwx="rw", mem="vram")
    assert bp.token() == "-b 0080FF:rw:vram"

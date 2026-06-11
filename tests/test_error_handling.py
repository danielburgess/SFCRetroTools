"""Error-handling contract tests (red-team unification, 2026-06-10).

The unified strategy:
  * Library code NEVER prints to stdout — diagnostics go to module loggers.
  * Address conversions return None for unmappable input (load-bearing API,
    unchanged) — silently, at any verbosity.
  * Malformed .tbl files raise TableParseError listing every bad line
    (strict default); strict=False restores skip-and-warn.
  * User-reachable validation raises typed errors (HandlerError), never
    `assert` (stripped under python -O).
  * PatchResult.check() turns the easy-to-ignore .ok flag into an exception.
"""
from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

import pytest

from retrotool.asm.patcher import PatchError, PatchResult
from retrotool.build.handlers import HandlerError
from retrotool.core.address import SFCAddress, SFCAddressType
from retrotool.script.table import Table, TableParseError


# ---------------------------------------------------------------------------
# core.address — None-returning, stdout-silent
# ---------------------------------------------------------------------------

def test_invalid_conversions_return_none_silently(capsys):
    # Unmappable inputs across converters — including the two that used to
    # print UNCONDITIONALLY (exlorom/exhirom) and the None-input guards.
    assert SFCAddress.lorom1_to_pc(0x004000) is None          # system area
    assert SFCAddress.lorom1_to_pc(None, verbose=True) is None
    assert SFCAddress.lorom2_to_pc(0x004000) is None
    assert SFCAddress.hirom_to_pc(0x003000, verbose=True) is None  # WRAM/IO
    assert SFCAddress.exlorom_to_pc(0x004000) is None
    assert SFCAddress.exhirom_to_pc(0x114000) is None
    assert SFCAddress.pc_to_lorom1(0x400000) is None          # past 4MB
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_verbose_diagnostics_go_to_logger(caplog):
    with caplog.at_level(logging.DEBUG, logger="retrotool.core.address"):
        # lru_cache memoizes (arg, verbose) pairs — use a fresh address so
        # the cached silent call above doesn't swallow the logging path.
        assert SFCAddress.lorom1_to_pc(0x005123, verbose=True) is None
    assert any("LoROM1" in r.message for r in caplog.records)


def test_valid_conversions_unchanged():
    # PC 0x8000 <-> canonical LoROM1 $01:8000 (banks $00-$6F; $81 mirrors).
    assert SFCAddress.pc_to_lorom1(0x8000) == 0x018000
    assert SFCAddress.lorom1_to_pc(0x018000) == 0x8000
    assert SFCAddress(0x8000, SFCAddressType.PC).get_bank_byte(
        SFCAddressType.LOROM1) == 0x01


# ---------------------------------------------------------------------------
# script.table — strict parse errors
# ---------------------------------------------------------------------------

BAD_TBL = (
    "41=A\n"
    "ZZZZ=bogus hex\n"        # int('ZZZZ', 16) -> ValueError
    "42=B\n"
    "@ctrl GG=2\n"            # bad hex in directive
)


def test_malformed_table_raises_with_line_numbers(tmp_path: Path):
    tbl = tmp_path / "broken.tbl"
    tbl.write_text(BAD_TBL, encoding="utf-8")
    with pytest.raises(TableParseError) as ei:
        Table(tbl)
    msg = str(ei.value)
    assert "2 malformed line(s)" in msg
    assert f"{tbl}:2" in msg and "ZZZZ" in msg
    assert f"{tbl}:4" in msg


def test_non_strict_skips_and_counts(tmp_path: Path, caplog):
    tbl = tmp_path / "broken.tbl"
    tbl.write_text(BAD_TBL, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="retrotool.script.table"):
        t = Table(tbl, strict=False)
    assert t.errors == 2
    assert t.get_value("A") == 0x41 and t.get_value("B") == 0x42
    assert sum("malformed" in r.message for r in caplog.records) == 2


def test_clean_table_has_no_errors(tmp_path: Path):
    tbl = tmp_path / "ok.tbl"
    tbl.write_text("41=A\n42=B\n@ctrl_prefix FF\n@ctrl 9C=5\n", encoding="utf-8")
    t = Table(tbl)
    assert t.errors == 0


def test_export_csv_propagates_io_errors(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(OSError):
        Table.export_csv("no/such/dir/out", [{"a": 1}])


# ---------------------------------------------------------------------------
# build — typed errors instead of asserts
# ---------------------------------------------------------------------------

def test_write_split_validates_with_handler_errors(tmp_path: Path):
    from retrotool.build.extract import _write_split
    from retrotool.build.spec import Section, SectionKind
    sec = Section(kind=SectionKind.INS, files=[PurePosixPath("a.bin")],
                  offset=None)
    with pytest.raises(HandlerError, match="requires offset"):
        _write_split(b"\x00" * 16, sec, tmp_path, [4])
    sec.offset = 0
    with pytest.raises(HandlerError, match="must match"):
        _write_split(b"\x00" * 16, sec, tmp_path, [4, 4])


# ---------------------------------------------------------------------------
# asm.patcher — PatchResult.check()
# ---------------------------------------------------------------------------

def test_patch_result_check_passes_through_on_ok(tmp_path: Path):
    r = PatchResult(ok=True, output_rom=tmp_path / "out.sfc")
    assert r.check() is r


def test_patch_result_check_raises_with_log(tmp_path: Path):
    r = PatchResult(ok=False, output_rom=tmp_path / "out.sfc",
                    log="error: label 'Foo' redefined")
    with pytest.raises(PatchError, match="label 'Foo' redefined") as ei:
        r.check()
    assert ei.value.result is r

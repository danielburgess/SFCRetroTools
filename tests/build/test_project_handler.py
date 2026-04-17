"""Phase 5.5 — `<project>` handler runs nested mbxml against current ROM."""
from __future__ import annotations

from pathlib import Path

import pytest

from retrotool.build.handlers import HandlerError, handle_project
from retrotool.build.spec import Section, SectionKind


def _section(src: str):
    return Section(kind=SectionKind.PROJECT, attrs={"src": src}, source="test")


def test_project_runs_nested_sections(tmp_path):
    (tmp_path / "patch.bin").write_bytes(b"\x42\x43")
    (tmp_path / "sub.mbxml").write_text(
        '<build><ins file="patch.bin" offset="100"/></build>'
    )
    rom = bytearray(b"\x00" * 0x200)
    handle_project(rom, _section("sub.mbxml"), tmp_path)
    assert rom[0x100:0x102] == b"\x42\x43"


def test_project_missing_src(tmp_path):
    rom = bytearray(b"\x00" * 0x100)
    with pytest.raises(HandlerError, match="project src not found"):
        handle_project(rom, _section("missing.mbxml"), tmp_path)


def test_project_no_src_attr_raises(tmp_path):
    s = Section(kind=SectionKind.PROJECT, source="test", attrs={})
    rom = bytearray(b"\x00" * 0x100)
    with pytest.raises(HandlerError, match="requires src"):
        handle_project(rom, s, tmp_path)


def test_project_respects_sub_condition(tmp_path):
    (tmp_path / "patch.bin").write_bytes(b"\xAA")
    (tmp_path / "sub.mbxml").write_text(
        '<build version="ja">'
        '  <ins file="patch.bin" offset="100" if="${version}==en"/>'
        '</build>'
    )
    rom = bytearray(b"\x00" * 0x200)
    handle_project(rom, _section("sub.mbxml"), tmp_path)
    assert rom[0x100] == 0  # condition false → skipped

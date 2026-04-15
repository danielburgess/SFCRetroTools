"""Phase 5.3 — `<include>` element."""
from __future__ import annotations

import pytest

from retrotool.mbuild.front_ends.mbxml import IncludeError, parse_mbxml


def test_include_splices_sections(tmp_path):
    (tmp_path / "shared.mbxml").write_text(
        '<build><ins file="a.bin" offset="100"/><rep file="b.bin" offset="200"/></build>'
    )
    (tmp_path / "main.mbxml").write_text(
        '<build name="t" original="r.sfc">'
        '  <include src="shared.mbxml"/>'
        '  <ins file="c.bin" offset="300"/>'
        '</build>'
    )
    spec = parse_mbxml(tmp_path / "main.mbxml")
    offsets = [s.offset for s in spec.sections]
    assert offsets == [0x100, 0x200, 0x300]


def test_include_nested(tmp_path):
    (tmp_path / "leaf.mbxml").write_text('<build><ins file="x.bin" offset="100"/></build>')
    (tmp_path / "mid.mbxml").write_text('<build><include src="leaf.mbxml"/></build>')
    (tmp_path / "main.mbxml").write_text(
        '<build name="t" original="r.sfc"><include src="mid.mbxml"/></build>'
    )
    spec = parse_mbxml(tmp_path / "main.mbxml")
    assert len(spec.sections) == 1
    assert spec.sections[0].offset == 0x100


def test_include_cycle_raises(tmp_path):
    (tmp_path / "a.mbxml").write_text('<build><include src="b.mbxml"/></build>')
    (tmp_path / "b.mbxml").write_text('<build><include src="a.mbxml"/></build>')
    (tmp_path / "main.mbxml").write_text(
        '<build name="t" original="r.sfc"><include src="a.mbxml"/></build>'
    )
    with pytest.raises(IncludeError, match="cycle"):
        parse_mbxml(tmp_path / "main.mbxml")


def test_include_missing_src_raises(tmp_path):
    (tmp_path / "main.mbxml").write_text(
        '<build name="t" original="r.sfc"><include src="missing.mbxml"/></build>'
    )
    with pytest.raises(IncludeError, match="not found"):
        parse_mbxml(tmp_path / "main.mbxml")


def test_include_inherits_parent_vars(tmp_path):
    (tmp_path / "shared.mbxml").write_text(
        '<build><ins file="${version}.bin" offset="100"/></build>'
    )
    (tmp_path / "main.mbxml").write_text(
        '<build name="t" version="en" original="r.sfc">'
        '  <include src="shared.mbxml"/>'
        '</build>'
    )
    spec = parse_mbxml(tmp_path / "main.mbxml")
    assert str(spec.sections[0].files[0]) == "en.bin"

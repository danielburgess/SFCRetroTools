"""CLI `-D NAME=VALUE` wiring — `retrotool build/extract -D key=val` feeds
the same `defines=` kwarg into both front-ends."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from retrotool.build import parse_defines as _parse_defines
from retrotool.build import load_spec as _load_spec


def test_parse_defines_basic():
    assert _parse_defines(["version=en"]) == {"version": "en"}


def test_parse_defines_multiple_last_wins():
    assert _parse_defines(["v=1", "v=2", "x=y"]) == {"v": "2", "x": "y"}


def test_parse_defines_value_may_contain_equals():
    # `partition("=")` keeps everything after the first `=` as value.
    assert _parse_defines(["expr=a==b"]) == {"expr": "a==b"}


def test_parse_defines_none_returns_empty():
    assert _parse_defines(None) == {}


def test_parse_defines_missing_equals_raises():
    with pytest.raises(ValueError, match="name=value"):
        _parse_defines(["bogus"])


def test_parse_defines_empty_name_raises():
    with pytest.raises(ValueError, match="non-empty name"):
        _parse_defines(["=value"])


def test_cli_defines_flow_through_toml(tmp_path: Path):
    toml_text = textwrap.dedent("""
        [rom]
        file = "base.sfc"

        [rom.build]
        version = "ja"

        [[rom.build.sections]]
        kind = "rep"
        offset = 0x100
        file = "${version}/patch.bin"
    """)
    (tmp_path / "project.toml").write_text(toml_text)
    spec, _ = _load_spec(tmp_path / "project.toml", defines={"version": "en"})
    assert str(spec.sections[0].files[0]) == "en/patch.bin"
    assert spec.vars["version"] == "en"


def test_cli_defines_flow_through_mbxml(tmp_path: Path):
    xml = (
        '<build original="base.sfc" version="ja">'
        '<rep file="${version}/patch.bin" offset="100"/>'
        '</build>'
    )
    (tmp_path / "project.mbxml").write_text(xml)
    spec, _ = _load_spec(tmp_path / "project.mbxml", defines={"version": "en"})
    assert str(spec.sections[0].files[0]) == "en/patch.bin"
    assert spec.vars["version"] == "en"

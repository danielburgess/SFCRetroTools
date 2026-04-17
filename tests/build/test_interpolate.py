"""Tests for variable interpolation + condition evaluator (Phase 5.1/5.2)."""
import pytest

from retrotool.build.front_ends.mbxml import parse_mbxml_string
from retrotool.build.interpolate import (
    InterpolationError,
    build_vars,
    evaluate_condition,
    interpolate,
    interpolate_attrs,
)


def test_interpolate_basic():
    assert interpolate("hello ${name}", {"name": "world"}) == "hello world"


def test_interpolate_multiple():
    assert interpolate("${a}-${b}", {"a": "x", "b": "y"}) == "x-y"


def test_interpolate_unknown_raises():
    with pytest.raises(InterpolationError, match=r"unknown variable \$\{missing\}"):
        interpolate("${missing}", {})


def test_interpolate_attrs():
    result = interpolate_attrs({"file": "${name}.bin", "offset": "$8000"}, {"name": "rom"})
    assert result == {"file": "rom.bin", "offset": "$8000"}


def test_build_vars_overrides():
    vars = build_vars({"version": "ja", "name": "test"}, {"version": "en"})
    assert vars == {"name": "test", "version": "en"}


def test_eval_condition_eq():
    assert evaluate_condition("${v}==en", {"v": "en"}) is True
    assert evaluate_condition("${v}==en", {"v": "ja"}) is False


def test_eval_condition_neq():
    assert evaluate_condition("${v}!=ja", {"v": "en"}) is True
    assert evaluate_condition("${v}!=en", {"v": "en"}) is False


def test_eval_condition_whitespace():
    assert evaluate_condition("${v} == en ", {"v": "en"}) is True


def test_eval_condition_no_op_raises():
    with pytest.raises(InterpolationError, match="must contain one of"):
        evaluate_condition("${v}", {"v": "x"})


def test_mbxml_interpolates_section_attrs():
    text = """<build name="demo" version="en" original="r.sfc">
      <ins file="${name}-${version}.bin" offset="8000"/>
    </build>"""
    spec = parse_mbxml_string(text)
    assert str(spec.sections[0].files[0]) == "demo-en.bin"


def test_mbxml_defines_override():
    text = """<build name="demo" version="ja" original="r.sfc">
      <ins file="${version}.bin" offset="8000"/>
    </build>"""
    spec = parse_mbxml_string(text, defines={"version": "en"})
    assert str(spec.sections[0].files[0]) == "en.bin"


def test_mbxml_condition_preserved_unrendered():
    """`if=` is kept literal — evaluated by build pipeline, not at parse time."""
    text = """<build name="demo" original="r.sfc">
      <ins file="x.bin" offset="8000" if="${version}==en"/>
    </build>"""
    spec = parse_mbxml_string(text, defines={"version": "ja"})
    assert spec.sections[0].condition == "${version}==en"

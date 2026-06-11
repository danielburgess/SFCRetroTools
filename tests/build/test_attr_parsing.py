"""Centralized attr parsing/validation (red-team #6).

Enum-like Section attrs are validated ONCE, at Section construction
(`spec.Section.__post_init__`), covering every path: TOML/MBXML front-ends,
DataDef resolution, and direct BuildSpec assembly. Boolean string attrs go
through one helper (`handlers._attr_bool`) — and a typo now raises instead
of silently meaning False.
"""
from __future__ import annotations

import textwrap

import pytest

from retrotool.build import parse_project_toml
from retrotool.build.handlers import HandlerError, _attr_bool
from retrotool.build.spec import Section, SectionKind, VALID_GROW


# ---------------------------------------------------------------------------
# Section.__post_init__ — grow / pointer_size
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("grow", [None, "replace", "insert"])
def test_valid_grow_accepted(grow):
    Section(kind=SectionKind.BIN, grow=grow)


def test_grow_fail_rejected_with_guidance():
    with pytest.raises(ValueError) as ei:
        Section(kind=SectionKind.BIN, grow="fail")
    msg = str(ei.value)
    assert "never implemented" in msg and '"replace" already' in msg


def test_grow_typo_rejected():
    with pytest.raises(ValueError, match="invalid grow='isertt'"):
        Section(kind=SectionKind.BIN, grow="isertt", source="proj.toml:12")


@pytest.mark.parametrize("size", [None, 2, 3])
def test_valid_pointer_size_accepted(size):
    Section(kind=SectionKind.SCRIPT, pointer_size=size)


def test_pointer_size_out_of_range_rejected():
    with pytest.raises(ValueError, match="pointer-size must be 2 or 3"):
        Section(kind=SectionKind.SCRIPT, pointer_size=5)


def test_valid_grow_constant_is_the_contract():
    assert VALID_GROW == ("replace", "insert")   # "fail" is gone, on purpose


def test_parser_surfaces_grow_validation(tmp_path):
    """The front-end path: grow= is rejected at parse time, not mid-build."""
    (tmp_path / "project.toml").write_text(textwrap.dedent("""
        [rom]
        file = "in.sfc"

        [[rom.build.sections]]
        kind = "bin"
        file = "x.bin"
        offset = 0x100
        grow = "fail"
    """), encoding="utf-8")
    with pytest.raises(ValueError, match="never implemented"):
        parse_project_toml(tmp_path / "project.toml")


# ---------------------------------------------------------------------------
# _attr_bool — one boolean coercion for all handlers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("v", ["1", "true", "TRUE", "yes", "on", " On "])
def test_attr_bool_truthy(v):
    assert _attr_bool(v, key="k") is True


@pytest.mark.parametrize("v", [None, "", "0", "false", "no", "off", " OFF "])
def test_attr_bool_falsy(v):
    assert _attr_bool(v, key="k") is False


def test_attr_bool_typo_raises():
    with pytest.raises(HandlerError, match="allow-shrink='ture'"):
        _attr_bool("ture", key="allow-shrink", source="proj.toml:7")

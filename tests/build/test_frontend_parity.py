"""Front-end parity: TOML and MBXML should produce equivalent `BuildSpec`s
for every feature that lives in both formats.

Coverage:
  - freespace ranges
  - global labels
  - ${var} interpolation (name/version/revision)
  - user defines override (-D equivalent)
"""
from __future__ import annotations

import textwrap

from retrotool.build import (
    parse_mbxml_string,
    parse_project_toml_dict,
)


def test_freespace_parity():
    """Same freespace list from both formats."""
    toml = parse_project_toml_dict({
        "rom": {"file": "base.sfc", "build": {
            "freespace": [[0x230000, 0x234000], [0x234000, 0x238000]],
        }}
    })
    xml = parse_mbxml_string(
        '<build original="base.sfc">'
        '<freespace lo="0x230000" hi="0x234000"/>'
        '<freespace lo="0x234000" hi="0x238000"/>'
        '</build>'
    )
    assert toml.freespace == xml.freespace == [
        (0x230000, 0x234000), (0x234000, 0x238000),
    ]


def test_labels_parity():
    """Global label registry populated identically."""
    toml = parse_project_toml_dict({
        "rom": {"file": "base.sfc", "build": {
            "labels": [
                {"name": "player_state", "at": 0x7E0010},
                {"name": "map_ptr", "at": "0x7E0100"},
            ],
        }}
    })
    xml = parse_mbxml_string(
        '<build original="base.sfc">'
        '<label name="player_state" at="0x7E0010"/>'
        '<label name="map_ptr" at="0x7E0100"/>'
        '</build>'
    )
    assert toml.labels == xml.labels == {
        "player_state": 0x7E0010, "map_ptr": 0x7E0100,
    }


def test_var_interpolation_parity():
    """${var} substitution works identically in both front-ends."""
    toml_text = textwrap.dedent("""
        [rom]
        name = "Demo"
        file = "base.sfc"

        [rom.build]
        version = "en"

        [[rom.build.sections]]
        kind = "rep"
        offset = 0x100
        file = "${version}/patch.bin"
    """)
    # parse_project_toml_dict needs tomllib-parsed dict; parse file instead.
    import tomllib
    toml = parse_project_toml_dict(tomllib.loads(toml_text))
    xml = parse_mbxml_string(
        '<build original="base.sfc" name="Demo" version="en">'
        '<rep file="${version}/patch.bin" offset="100"/>'
        '</build>'
    )
    # Both should interpolate version="en" into the file path.
    assert str(toml.sections[0].files[0]) == "en/patch.bin"
    assert str(xml.sections[0].files[0]) == "en/patch.bin"


def test_defines_override_parity():
    """User defines override file-declared vars in both front-ends."""
    import tomllib
    toml_text = """
[rom]
file = "base.sfc"

[rom.build]
version = "ja"

[[rom.build.sections]]
kind = "rep"
offset = 0x100
file = "${version}/patch.bin"
"""
    toml = parse_project_toml_dict(
        tomllib.loads(toml_text), defines={"version": "en"},
    )
    xml = parse_mbxml_string(
        '<build original="base.sfc" version="ja">'
        '<rep file="${version}/patch.bin" offset="100"/>'
        '</build>',
        defines={"version": "en"},
    )
    assert str(toml.sections[0].files[0]) == "en/patch.bin"
    assert str(xml.sections[0].files[0]) == "en/patch.bin"
    assert toml.vars["version"] == "en"
    assert xml.vars["version"] == "en"


def test_mbxml_freespace_missing_attrs_raises():
    from retrotool.build.front_ends.schema import SchemaError
    import pytest
    with pytest.raises(SchemaError, match="<freespace>"):
        parse_mbxml_string(
            '<build original="base.sfc">'
            '<freespace lo="0x100"/>'  # missing hi=
            '</build>'
        )


def test_mbxml_label_missing_attrs_raises():
    from retrotool.build.front_ends.schema import SchemaError
    import pytest
    with pytest.raises(SchemaError, match="<label>"):
        parse_mbxml_string(
            '<build original="base.sfc">'
            '<label at="0x7E0010"/>'  # missing name=
            '</build>'
        )

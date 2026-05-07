"""DataDef `[section]` → pipeline Section synthesis, merging, and ordering."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from retrotool.build import (
    BuildSpec,
    Section,
    SectionKind,
    apply_datadefs_to_spec,
    merge_sections,
    parse_project_toml_dict,
    sections_from_datadefs,
)
from retrotool.build.front_ends.schema import SchemaError
from retrotool.project.datadef import (
    BuildStep,
    DataDef,
    DataSection,
    EncodingSection,
    PointersSection,
)


def _script_dd(name="scene-desc-name", offset=0x111EE3):
    return DataDef(
        name=name,
        type="pointer",
        encoding=EncodingSection(
            table_file=Path("en_data/eng.tbl"),
            fallback=Path("jp_data/jap.tbl"),
            terminator=0x00,
        ),
        pointers=PointersSection(offset=offset, count=158, size=2),
        section=BuildStep(kind="script", file="scene.txt"),
        extras={"word_wrap": {"line_width": 26, "max_lines": 6}, "textbuf_limit": 0x1F0},
    )


def _fixed_dd(name="unit-names", offset=0x220000):
    return DataDef(
        name=name,
        type="fixed",
        encoding=EncodingSection(table_file=Path("en_data/eng.tbl")),
        data=DataSection(offset=offset),
        section=BuildStep(kind="fixed-records", file="unit.bin", grow="insert"),
        extras={"entries": 146, "block_len": 0x10},
    )


def _extract_only_dd(name="docs-only", offset=0x100):
    # No [section] — should be skipped by the pipeline.
    return DataDef(
        name=name, type="pointer",
        pointers=PointersSection(offset=offset, count=4, size=2),
    )


def test_script_section_synthesized():
    secs = sections_from_datadefs([_script_dd()])
    assert len(secs) == 1
    s = secs[0]
    assert s.kind is SectionKind.SCRIPT
    assert s.pointer_table == 0x111EE3
    assert s.pointer_size == 2
    assert s.count == 158
    assert s.table == PurePosixPath("en_data/eng.tbl")
    assert s.fallback_table == PurePosixPath("jp_data/jap.tbl")
    assert s.terminator == 0x00
    assert s.word_wrap == {"line_width": 26, "max_lines": 6}
    assert s.textbuf_limit == 0x1F0
    assert s.files == [PurePosixPath("scene.txt")]
    assert s.from_datadef == "scene-desc-name"


def test_fixed_section_synthesized():
    secs = sections_from_datadefs([_fixed_dd()])
    s = secs[0]
    assert s.kind is SectionKind.FIXED_RECORDS
    assert s.offset == 0x220000
    assert s.stride == 0x10
    assert s.count == 146
    assert s.grow == "insert"


def test_extract_only_datadef_skipped():
    secs = sections_from_datadefs([_extract_only_dd(), _script_dd()])
    assert [s.from_datadef for s in secs] == ["scene-desc-name"]


def test_merge_orders_by_offset():
    secs = sections_from_datadefs([_fixed_dd(), _script_dd()])
    # unit-names @ 0x220000, scene-desc-name @ 0x111EE3 — by offset, script first.
    merged = merge_sections([], secs)
    assert [s.from_datadef for s in merged] == ["scene-desc-name", "unit-names"]


def test_merge_respects_explicit_order():
    secs = sections_from_datadefs([_fixed_dd(), _script_dd()])
    merged = merge_sections([], secs, order=["unit-names", "scene-desc-name"])
    assert [s.from_datadef for s in merged] == ["unit-names", "scene-desc-name"]


def test_merge_interleaves_inline_and_datadef():
    inline = [Section(kind=SectionKind.REP, offset=0x500,
                      files=[PurePosixPath("patch.bin")])]
    dd_secs = sections_from_datadefs([_script_dd(offset=0x300)])
    merged = merge_sections(inline, dd_secs)
    # script @ 0x300, inline @ 0x500 → script first
    assert merged[0].from_datadef == "scene-desc-name"
    assert merged[1].offset == 0x500


def test_merge_duplicate_offset_rejected():
    inline = [Section(kind=SectionKind.REP, offset=0x111EE3,
                      files=[PurePosixPath("a.bin")])]
    dd_secs = sections_from_datadefs([_script_dd()])
    with pytest.raises(SchemaError, match="two sections patch offset"):
        merge_sections(inline, dd_secs)


def test_merge_unknown_order_entry_rejected():
    dd_secs = sections_from_datadefs([_script_dd()])
    with pytest.raises(SchemaError, match="unknown sections"):
        merge_sections([], dd_secs, order=["does-not-exist"])


def test_apply_datadefs_to_spec_populates_pipeline():
    spec = parse_project_toml_dict({"rom": {"file": "base.sfc", "build": {}}})
    apply_datadefs_to_spec(spec, [_script_dd(), _fixed_dd()])
    assert len(spec.sections) == 2
    assert spec.sections[0].from_datadef == "scene-desc-name"


def test_apply_datadefs_with_order_from_spec():
    data = {"rom": {"file": "base.sfc", "build": {
        "order": ["unit-names", "scene-desc-name"],
    }}}
    spec = parse_project_toml_dict(data)
    apply_datadefs_to_spec(spec, [_script_dd(), _fixed_dd()], order=spec.order)
    assert [s.from_datadef for s in spec.sections] == ["unit-names", "scene-desc-name"]


# ---- DataDef [section] parsing --------------------------------------------


def test_datadef_section_parses():
    from retrotool.project.datadef import datadef_from_dict
    dd = datadef_from_dict({
        "table": {"name": "x", "type": "pointer"},
        "pointers": {"offset": 0x100, "count": 10, "size": 2},
        "section": {"kind": "script", "file": "s.txt",
                    "overflow": {"strategy": "inline-redirect"}},
    })
    assert dd.section is not None
    assert dd.section.kind == "script"
    assert dd.section.file == "s.txt"
    assert dd.section.overflow == {"strategy": "inline-redirect"}


def test_datadef_section_rejects_rom_structure_keys():
    from retrotool.project.datadef import datadef_from_dict
    for forbidden in ("pointer-table", "pointer-size", "count", "table",
                      "fallback-table", "terminator", "word-wrap",
                      "textbuf-limit", "stride"):
        with pytest.raises(ValueError, match="cannot redeclare"):
            datadef_from_dict({
                "table": {"name": "x", "type": "pointer"},
                "section": {"kind": "script", forbidden: "anything"},
            })


def test_datadef_section_requires_kind():
    from retrotool.project.datadef import datadef_from_dict
    with pytest.raises(ValueError, match=r"\[section\]\.kind required"):
        datadef_from_dict({
            "table": {"name": "x", "type": "pointer"},
            "section": {"file": "s.txt"},
        })


def test_datadef_pointers_requires_offset():
    from retrotool.project.datadef import datadef_from_dict
    with pytest.raises(ValueError, match="was 'address' in legacy schema"):
        datadef_from_dict({
            "table": {"name": "x", "type": "pointer"},
            "pointers": {"address": 0x100, "count": 1, "size": 2},
        })


def test_datadef_data_requires_offset():
    from retrotool.project.datadef import datadef_from_dict
    with pytest.raises(ValueError, match="was 'start' in legacy schema"):
        datadef_from_dict({
            "table": {"name": "x", "type": "fixed"},
            "data": {"start": 0x100},
        })


def test_anchor_offset_pointers_takes_precedence():
    dd = DataDef(
        name="x", type="pointer",
        pointers=PointersSection(offset=0x200, count=1, size=2),
        data=DataSection(offset=0x300),
    )
    assert dd.anchor_offset == 0x200


def test_anchor_offset_falls_back_to_section_offset():
    dd = DataDef(
        name="x", type="pointer",
        section=BuildStep(kind="rep", offset=0x400),
    )
    assert dd.anchor_offset == 0x400

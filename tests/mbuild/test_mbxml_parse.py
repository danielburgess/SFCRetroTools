"""Phase 1 tests: MBXML parser produces a valid BuildSpec covering all MBuild 1.29
element kinds, plus acceptance parse of the real MBuild sample file.

Round-trip extraction + actual build logic live in later phases.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from retrotool.mbuild import BuildSpec, SectionKind
from retrotool.mbuild.front_ends.mbxml import parse_mbxml, parse_mbxml_string
from retrotool.mbuild.front_ends.schema import SchemaError


MBUILD_SAMPLE = Path("/mnt/crucial/projects/MBuild/MBuild.MBXML")


def test_parses_build_root_attrs():
    xml = """<build original="base.sfc" name="Demo" version="v1" revision="01"
                    revbyteloc="7FDB" path="..\\out" pad="true" diff="ips">
      <rep file="DATA\\patch.bin" offset="11E3"/>
    </build>"""
    spec = parse_mbxml_string(xml)
    assert spec.name == "Demo"
    assert spec.version == "v1"
    assert spec.revision == "01"
    assert spec.revbyteloc == 0x7FDB
    assert spec.pad is True
    assert spec.diff == "ips"
    assert spec.original == PurePosixPath("base.sfc")
    assert spec.path == PurePosixPath("../out")
    assert len(spec.sections) == 1


def test_rep_offset_hex_no_prefix():
    spec = parse_mbxml_string('<build><rep file="a.bin" offset="11E3"/></build>')
    sec = spec.sections[0]
    assert sec.kind is SectionKind.REP
    assert sec.offset == 0x11E3
    assert sec.files == [PurePosixPath("a.bin")]


def test_ins_multi_file_concat():
    xml = '<build><ins file="A.bin|B.bin|C.bin" offset="300000"/></build>'
    spec = parse_mbxml_string(xml)
    assert spec.sections[0].files == [
        PurePosixPath("A.bin"),
        PurePosixPath("B.bin"),
        PurePosixPath("C.bin"),
    ]
    assert spec.sections[0].offset == 0x300000


def test_all_mbuild_section_kinds_auto_migrate():
    """MBuild 1.29 legacy elements are accepted but auto-migrated to unified form."""
    xml = """<build>
      <rep file="a" offset="0"/>
      <ins file="a" offset="1"/>
      <lzr file="a" offset="2" lztype="lzss-zamn"/>
      <lzi file="a" offset="3" lztype="lzss-zamn"/>
      <rlr file="a" offset="4" rletype="mbuild"/>
      <rli file="a" offset="5" rletype="mbuild"/>
      <bpr file="a" offset="6" bptype="2bpp-to-1bpp-il"/>
      <bpi file="a" offset="7" bptype="2bpp-to-1bpp-il"/>
      <sbr file="a" offset="8" table="t.tbl"/>
      <sbi file="a" offset="9" table="t.tbl"/>
    </build>"""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = parse_mbxml_string(xml)
    kinds = [s.kind for s in spec.sections]
    # rep/ins keep their kind; codec-bearing legacy elements migrate.
    assert kinds == [
        SectionKind.REP, SectionKind.INS,
        SectionKind.BIN, SectionKind.BIN,
        SectionKind.BIN, SectionKind.BIN,
        SectionKind.GRAPHICS, SectionKind.GRAPHICS,
        SectionKind.SCRIPT, SectionKind.SCRIPT,
    ]
    # provenance retained
    assert spec.sections[2].original_kind is SectionKind.LZR
    assert spec.sections[2].codec == "lzss-zamn"
    assert spec.sections[2].grow == "replace"
    assert spec.sections[3].grow == "insert"
    assert spec.sections[8].table == PurePosixPath("t.tbl")
    assert spec.sections[8].grow == "replace"


def test_windows_paths_normalized():
    xml = '<build><rep file="BIN DATA\\sub\\file.bin" offset="100"/></build>'
    spec = parse_mbxml_string(xml)
    assert spec.sections[0].files == [PurePosixPath("BIN DATA/sub/file.bin")]


def test_comments_skipped():
    xml = """<build>
      <!-- header -->
      <rep file="a" offset="0"/>
      <!-- trailing -->
    </build>"""
    spec = parse_mbxml_string(xml)
    assert len(spec.sections) == 1


def test_missing_required_attr_raises():
    with pytest.raises(SchemaError, match="missing required"):
        parse_mbxml_string('<build><rep file="a"/></build>')


def test_unknown_element_raises():
    with pytest.raises(SchemaError, match="unknown element"):
        parse_mbxml_string('<build><nope file="a" offset="0"/></build>')


def test_strict_rejects_unknown_attrs():
    xml = '<build><rep file="a" offset="0" bogus="x"/></build>'
    # non-strict: accepted
    parse_mbxml_string(xml, strict=False)
    with pytest.raises(SchemaError, match="unknown attrs"):
        parse_mbxml_string(xml, strict=True)


def test_root_must_be_build():
    with pytest.raises(SchemaError, match="root element"):
        parse_mbxml_string("<notbuild/>")


def test_retrotool_extension_kinds():
    xml = """<build>
      <bin file="a" offset="100" size="32"/>
      <asar file="patch.asm"/>
      <graphics file="tiles.bin" offset="200" bpp="4"/>
      <libsfx src="./game_src" debug="2" out="@rom"/>
    </build>"""
    spec = parse_mbxml_string(xml)
    assert [s.kind for s in spec.sections] == [
        SectionKind.BIN, SectionKind.ASAR,
        SectionKind.GRAPHICS, SectionKind.LIBSFX,
    ]
    assert spec.sections[0].size == 32
    assert spec.sections[2].bpp == 4
    assert spec.sections[3].files == [PurePosixPath("./game_src")]


@pytest.mark.skipif(not MBUILD_SAMPLE.exists(), reason="MBuild reference sample not present")
def test_parses_real_mbuild_sample():
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = parse_mbxml(MBUILD_SAMPLE)
    assert isinstance(spec, BuildSpec)
    assert spec.name == "MarvelousATI"
    assert spec.pad is True
    assert spec.revbyteloc == 0x7FDB
    # sample contains rep + lzr (auto-migrated to BIN) + ins
    kinds = {s.kind for s in spec.sections}
    assert {SectionKind.REP, SectionKind.BIN, SectionKind.INS} <= kinds
    # at least one BIN section came from <lzr>
    assert any(
        s.kind is SectionKind.BIN and s.original_kind is SectionKind.LZR
        for s in spec.sections
    )
    # all sections parsed offsets
    assert all(s.offset is not None for s in spec.sections)


# ---- deprecation handling --------------------------------------------------

from retrotool.mbuild import (
    MBXMLDeprecationWarning,
    migrate_mbxml,
    migrate_mbxml_string,
)


def test_legacy_element_emits_deprecation_warning():
    xml = '<build><lzr file="a" offset="100" lztype="lzss-zamn"/></build>'
    with pytest.warns(MBXMLDeprecationWarning, match=r"<lzr>.*Prefer unified style"):
        spec = parse_mbxml_string(xml)
    # Despite the warning, parsing succeeds and migration happened.
    assert spec.sections[0].kind is SectionKind.BIN
    assert spec.sections[0].codec == "lzss-zamn"


def test_rep_and_ins_do_not_warn():
    xml = """<build>
      <rep file="a" offset="0"/>
      <ins file="b" offset="100"/>
    </build>"""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error", MBXMLDeprecationWarning)
        # Should not raise — rep/ins are not deprecated.
        parse_mbxml_string(xml)


def test_deprecations_ignore_silences():
    xml = '<build><lzr file="a" offset="0" lztype="x"/></build>'
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error", MBXMLDeprecationWarning)
        # ignore mode → no warning even when filter would raise.
        parse_mbxml_string(xml, deprecations="ignore")


def test_deprecations_error_raises_schema_error():
    xml = '<build><lzr file="a" offset="0" lztype="x"/></build>'
    with pytest.raises(SchemaError, match=r"<lzr>.*legacy"):
        parse_mbxml_string(xml, deprecations="error")


def test_migrate_mbxml_string_rewrites_legacy_elements():
    xml = """<build>
      <rep file="a" offset="0"/>
      <lzr file="b" offset="100" lztype="lzss-zamn"/>
      <bpi file="c" offset="200" bptype="2bpp-to-1bpp-il"/>
      <sbr file="d" offset="300" table="t.tbl"/>
    </build>"""
    out = migrate_mbxml_string(xml)
    # Source-level checks: legacy tags gone, unified tags present.
    assert "<lzr" not in out and "<bpi" not in out and "<sbr" not in out
    assert 'codec="lzss-zamn"' in out
    assert 'grow="replace"' in out and 'grow="insert"' in out
    # rep is left untouched.
    assert "<rep" in out
    # The migrated text must itself parse without warnings.
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error", MBXMLDeprecationWarning)
        spec = parse_mbxml_string(out)
    assert [s.kind for s in spec.sections] == [
        SectionKind.REP, SectionKind.BIN, SectionKind.GRAPHICS, SectionKind.SCRIPT,
    ]


def test_migrate_mbxml_in_place_creates_backup(tmp_path):
    src = tmp_path / "build.mbxml"
    src.write_text('<build><lzi file="a" offset="0" lztype="lzss-zamn"/></build>')
    migrate_mbxml(src, in_place=True)
    backup = tmp_path / "build.mbxml.bak"
    assert backup.exists()
    assert "<lzi" in backup.read_text()
    assert "<lzi" not in src.read_text()
    assert 'grow="insert"' in src.read_text()

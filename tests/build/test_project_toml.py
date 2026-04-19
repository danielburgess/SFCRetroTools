"""Phase 2c: project.toml front-end tests.

Same end-to-end contract as MBXML — parse to BuildSpec, run build/extract
through the existing handlers, bytes match."""
from __future__ import annotations

import textwrap
from pathlib import Path, PurePosixPath

import pytest

from retrotool.build import (
    BuildSpec,
    Section,
    SectionKind,
    build,
    extract,
    parse_project_toml,
    parse_project_toml_dict,
)
from retrotool.build.front_ends.schema import SchemaError


_ROM_SIZE = 0x80_000


def _make_lorom(tmp_path: Path) -> Path:
    body = bytearray([0x00] * _ROM_SIZE)
    body[0x7FC0:0x7FC0 + 21] = b"TEST ROM             "
    body[0x7FD5] = 0x20
    body[0x7FD7] = 0x09
    body[0x100:0x108] = b"\xDE\xAD\xBE\xEF\xCA\xFE\xBA\xBE"
    body[0x7FDC:0x7FE0] = b"\xFF\xFF\x00\x00"
    csum = sum(body) & 0xFFFF
    body[0x7FDC] = (csum ^ 0xFFFF) & 0xFF
    body[0x7FDD] = ((csum ^ 0xFFFF) >> 8) & 0xFF
    body[0x7FDE] = csum & 0xFF
    body[0x7FDF] = (csum >> 8) & 0xFF
    p = tmp_path / "base.sfc"
    p.write_bytes(body)
    return p


def test_parse_scalar_build_fields(tmp_path):
    toml_text = textwrap.dedent("""
        [rom]
        name = "Demo"
        file = "base.sfc"

        [rom.build]
        version = "v1"
        revision = "01"
        revbyteloc = 0x7FDB
        pad = true
        diff = "ips"
    """)
    p = tmp_path / "project.toml"
    p.write_text(toml_text)
    spec = parse_project_toml(p)
    assert spec.name == "Demo"
    assert spec.version == "v1"
    assert spec.revision == "01"
    assert spec.revbyteloc == 0x7FDB
    assert spec.pad is True
    assert spec.diff == "ips"
    assert spec.original == PurePosixPath("base.sfc")


def test_section_file_as_string_or_list():
    data = {
        "rom": {"file": "base.sfc", "build": {
            "sections": [
                {"kind": "rep", "offset": 0x100, "file": "single.bin"},
                {"kind": "ins", "offset": 0x200, "file": ["a.bin", "b.bin"]},
            ]
        }}
    }
    spec = parse_project_toml_dict(data)
    assert spec.sections[0].files == [PurePosixPath("single.bin")]
    assert spec.sections[1].files == [PurePosixPath("a.bin"), PurePosixPath("b.bin")]


def test_offset_hex_string_accepted():
    data = {"rom": {"file": "base.sfc", "build": {"sections": [
        {"kind": "rep", "offset": "0x11E3", "file": "a.bin"},
        {"kind": "rep", "offset": "11E3", "file": "b.bin"},  # MBuild raw-hex
    ]}}}
    spec = parse_project_toml_dict(data)
    assert spec.sections[0].offset == 0x11E3
    assert spec.sections[1].offset == 0x11E3


def test_missing_build_table_raises(tmp_path):
    p = tmp_path / "project.toml"
    p.write_text("[other]\nfoo = 1\n")
    with pytest.raises(SchemaError, match="no \\[rom\\.build\\] table"):
        parse_project_toml(p)


def test_original_in_rom_build_rejected():
    data = {"rom": {"file": "base.sfc", "build": {"original": "other.sfc"}}}
    with pytest.raises(SchemaError, match=r"\[rom\.build\]\.original"):
        parse_project_toml_dict(data)


def test_name_in_rom_build_rejected():
    data = {"rom": {"name": "Demo", "file": "base.sfc",
                    "build": {"name": "Other"}}}
    with pytest.raises(SchemaError, match=r"\[rom\.build\]\.name"):
        parse_project_toml_dict(data)


def test_unknown_kind_raises():
    data = {"rom": {"file": "base.sfc", "build": {
        "sections": [{"kind": "frobnicate", "offset": 0}]}}}
    with pytest.raises(SchemaError, match="unknown kind"):
        parse_project_toml_dict(data)


def test_missing_kind_raises():
    data = {"rom": {"file": "base.sfc", "build": {
        "sections": [{"offset": 0}]}}}
    with pytest.raises(SchemaError, match="missing kind"):
        parse_project_toml_dict(data)


def test_unified_bin_section_roundtrip():
    data = {
        "rom": {"file": "base.sfc", "build": {
            "sections": [{
                "kind": "bin", "offset": "0x200000",
                "file": "chunk.bin", "codec": "lzss-zamn",
                "grow": "replace", "size": 256,
            }]
        }}
    }
    spec = parse_project_toml_dict(data)
    sec = spec.sections[0]
    assert sec.kind is SectionKind.BIN
    assert sec.codec == "lzss-zamn"
    assert sec.grow == "replace"
    assert sec.size == 256


# ---- end-to-end parity with MBXML ----------------------------------------


def test_smoke_build_from_project_toml(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "patch.bin").write_bytes(b"\xCA\xFE\xBA\xBE")
    p = tmp_path / "project.toml"
    p.write_text(textwrap.dedent(f"""
        [rom]
        name = "Demo"
        file = "{rom_path.name}"

        [rom.build]
        pad = true

        [[rom.build.sections]]
        kind = "rep"
        offset = 0x600
        file = "patch.bin"
    """))
    spec = parse_project_toml(p)
    out = tmp_path / "demo.sfc"
    build(spec, source_root=tmp_path, out_path=out)
    assert out.read_bytes()[0x600:0x604] == b"\xCA\xFE\xBA\xBE"


def test_toml_and_mbxml_produce_equivalent_spec(tmp_path):
    """Equivalent inputs → byte-identical output ROMs."""
    from retrotool.build import parse_mbxml
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "patch.bin").write_bytes(b"\x12\x34\x56\x78")

    mbxml_path = tmp_path / "build.mbxml"
    mbxml_path.write_text(
        f'<build original="{rom_path.name}" pad="true">'
        '<rep file="patch.bin" offset="500"/>'
        '</build>'
    )
    toml_path = tmp_path / "project.toml"
    toml_path.write_text(textwrap.dedent(f"""
        [rom]
        file = "{rom_path.name}"

        [rom.build]
        pad = true

        [[rom.build.sections]]
        kind = "rep"
        offset = 0x500
        file = "patch.bin"
    """))

    spec_xml = parse_mbxml(mbxml_path)
    spec_toml = parse_project_toml(toml_path)

    out_xml = tmp_path / "from_xml.sfc"
    out_toml = tmp_path / "from_toml.sfc"
    build(spec_xml, source_root=tmp_path, out_path=out_xml)
    build(spec_toml, source_root=tmp_path, out_path=out_toml)
    assert out_xml.read_bytes() == out_toml.read_bytes()


def test_extract_via_toml_frontend(tmp_path):
    rom_path = _make_lorom(tmp_path)
    data = {
        "rom": {"file": rom_path.name, "build": {
            "sections": [{
                "kind": "rep", "offset": "0x100", "size": 8,
                "file": "dump.bin",
            }],
        }}
    }
    spec = parse_project_toml_dict(data)
    extract(spec, source_root=tmp_path, original_rom=rom_path)
    assert (tmp_path / "dump.bin").read_bytes() == b"\xDE\xAD\xBE\xEF\xCA\xFE\xBA\xBE"


# ---- include mechanism ----------------------------------------------------


def test_include_splices_sections(tmp_path):
    from retrotool.build import parse_project_toml
    (tmp_path / "tables").mkdir()
    (tmp_path / "tables" / "extra.toml").write_text(
        '[rom.build]\n'
        '[[rom.build.sections]]\nkind="rep"\noffset=0x200\nfile="b.bin"\n',
        encoding="utf-8",
    )
    (tmp_path / "project.toml").write_text(
        '[rom]\n'
        'file="base.sfc"\n'
        '[rom.build]\n'
        'include = ["tables/extra.toml"]\n'
        '[[rom.build.sections]]\nkind="rep"\noffset=0x100\nfile="a.bin"\n',
        encoding="utf-8",
    )
    spec = parse_project_toml(tmp_path / "project.toml")
    assert [s.offset for s in spec.sections] == [0x100, 0x200]


def test_include_cycle_detected(tmp_path):
    from retrotool.build import parse_project_toml
    from retrotool.build.front_ends.schema import SchemaError
    (tmp_path / "a.toml").write_text(
        '[rom.build]\ninclude = ["b.toml"]\n', encoding="utf-8"
    )
    (tmp_path / "b.toml").write_text(
        '[rom.build]\ninclude = ["a.toml"]\n', encoding="utf-8"
    )
    with pytest.raises(SchemaError, match="cycle"):
        parse_project_toml(tmp_path / "a.toml")


def test_parse_script_section_extras(tmp_path):
    toml_text = textwrap.dedent("""
        [rom]
        file = "base.sfc"

        [rom.build]
        freespace = [[0x230000, 0x234000], [0x234000, 0x238000]]

        [[rom.build.sections]]
        kind = "script"
        file = "scene-desc-name.txt"
        table = "eng.tbl"
        fallback-table = "jap.tbl"
        pointer-table = 0x111EE3
        pointer-size = 2
        count = 158
        terminator = 0x00
        textbuf-limit = 0x1F0
        word-wrap = { line-width = 26, max-lines = 6, entries = "0-56" }

        [rom.build.sections.overflow]
        strategy = "inline-redirect"
        marker = [0xFF, 0xC0]
        splitter = "split-at-last-marker-byte"
        splitter-arg = 0x10
    """)
    (tmp_path / "project.toml").write_text(toml_text, encoding="utf-8")
    spec = parse_project_toml(tmp_path / "project.toml")
    assert spec.freespace == [(0x230000, 0x234000), (0x234000, 0x238000)]
    s = spec.sections[0]
    assert s.kind == SectionKind.SCRIPT
    assert s.pointer_table == 0x111EE3
    assert s.pointer_size == 2
    assert s.count == 158
    assert s.terminator == 0x00
    assert s.textbuf_limit == 0x1F0
    assert s.fallback_table == PurePosixPath("jap.tbl")
    assert s.word_wrap == {"line_width": 26, "max_lines": 6, "entries": "0-56"}
    assert s.overflow["strategy"] == "inline-redirect"
    assert s.overflow["splitter"] == "split-at-last-marker-byte"


def test_section_datadef_keyword_rejected():
    """`datadef=` on an inline section is no longer accepted — DataDefs
    auto-include via their own `[section]` sub-table now."""
    data = {"rom": {"file": "base.sfc", "build": {
        "sections": [{"kind": "script", "datadef": "x", "file": "s.txt"}]
    }}}
    with pytest.raises(SchemaError, match="no longer accepted"):
        parse_project_toml_dict(data)


def test_order_parsed(tmp_path):
    (tmp_path / "project.toml").write_text(textwrap.dedent("""
        [rom]
        file = "base.sfc"

        [rom.build]
        order = ["alpha", "beta"]
    """), encoding="utf-8")
    spec = parse_project_toml(tmp_path / "project.toml")
    assert spec.order == ["alpha", "beta"]


def test_order_type_validated():
    data = {"rom": {"file": "base.sfc", "build": {"order": "alpha"}}}
    with pytest.raises(SchemaError, match="order must be a list"):
        parse_project_toml_dict(data)


def test_freespace_invalid_pair(tmp_path):
    (tmp_path / "project.toml").write_text(
        '[rom]\nfile="base.sfc"\n[rom.build]\nfreespace=[[0x100, 0x100]]\n',
        encoding="utf-8"
    )
    with pytest.raises(SchemaError, match="invalid range"):
        parse_project_toml(tmp_path / "project.toml")


def test_mesen_table_parsed(tmp_path):
    (tmp_path / "project.toml").write_text(textwrap.dedent("""
        [rom]
        file = "base.sfc"

        [rom.build]
        pad = true

        [mesen]
        sync-sram = true
        saves-dir = "~/.config/Mesen2/Saves"
    """), encoding="utf-8")
    spec = parse_project_toml(tmp_path / "project.toml")
    assert spec.sync_sram is True
    assert spec.mesen_saves_dir == "~/.config/Mesen2/Saves"
    assert spec.archive_sram is True  # default when [mesen] omits the key


def test_mesen_archive_disabled():
    data = {"rom": {"file": "base.sfc", "build": {}},
            "mesen": {"sync-sram": True, "archive-overwritten": False}}
    spec = parse_project_toml_dict(data)
    assert spec.sync_sram is True
    assert spec.archive_sram is False


def test_mesen_table_absent_defaults_off():
    data = {"rom": {"file": "base.sfc", "build": {"pad": True}}}
    spec = parse_project_toml_dict(data)
    assert spec.sync_sram is False
    assert spec.mesen_saves_dir is None


def test_mesen_sync_sram_off_explicit():
    data = {"rom": {"file": "base.sfc", "build": {}},
            "mesen": {"sync-sram": False}}
    spec = parse_project_toml_dict(data)
    assert spec.sync_sram is False


def test_mesen_table_type_validated():
    data = {"rom": {"file": "base.sfc", "build": {}}, "mesen": "oops"}
    with pytest.raises(SchemaError, match=r"\[mesen\] must be a table"):
        parse_project_toml_dict(data)


def test_build_syncs_sram_post_write(tmp_path):
    rom_path = _make_lorom(tmp_path)
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "base.srm").write_bytes(b"save-state")

    data = {"rom": {"file": rom_path.name, "build": {"pad": True}},
            "mesen": {"sync-sram": True, "saves-dir": str(saves)}}
    spec = parse_project_toml_dict(data)
    out = tmp_path / "patched.sfc"
    build(spec, source_root=tmp_path, out_path=out,
          original_rom=rom_path)
    assert (saves / "patched.srm").read_bytes() == b"save-state"
    # Source untouched.
    assert (saves / "base.srm").read_bytes() == b"save-state"


def test_build_sram_sync_off_by_default(tmp_path):
    rom_path = _make_lorom(tmp_path)
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "base.srm").write_bytes(b"save-state")

    data = {"rom": {"file": rom_path.name, "build": {"pad": True}}}
    spec = parse_project_toml_dict(data)
    out = tmp_path / "patched.sfc"
    build(spec, source_root=tmp_path, out_path=out,
          original_rom=rom_path)
    assert not (saves / "patched.srm").exists()

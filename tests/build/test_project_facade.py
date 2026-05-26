"""Tests for the `retrotool.build` project-level facade.

These mirror the most useful CLI flows so a Python user driving retrotool
from a notebook or higher-level script gets the same result + summary
output as `retrotool <cmd>` would produce.
"""
from __future__ import annotations

import io
import textwrap
from pathlib import Path

import pytest

from retrotool.build import (
    BuildResult,
    build_project,
    default_cache_dir,
    default_output_path,
    extract_project,
    iter_step_builds,
    load_spec,
    migrate_project,
    parse_csv_set,
    parse_defines,
    resolve_extract_dest,
    resolve_jobs,
    resolve_spec_path,
    workers_for_print,
)
from tests.build.conftest import _make_lorom


# ---- Helpers ---------------------------------------------------------------

def test_resolve_spec_path_picks_toml_over_mbxml(tmp_path):
    (tmp_path / "project.toml").write_text("[rom]\n")
    (tmp_path / "x.mbxml").write_text("<build/>")
    p, kind = resolve_spec_path(tmp_path)
    assert p.name == "project.toml" and kind == "toml"


def test_resolve_spec_path_rejects_unknown_extension(tmp_path):
    f = tmp_path / "spec.yaml"
    f.write_text("nope")
    with pytest.raises(ValueError):
        resolve_spec_path(f)


def test_parse_defines_dict_from_list():
    assert parse_defines(["v=1", "v=2", "x=y"]) == {"v": "2", "x": "y"}


def test_parse_csv_set_accepts_string_list_set():
    assert parse_csv_set("a,b,c") == {"a", "b", "c"}
    assert parse_csv_set(["a", "b"]) == {"a", "b"}
    assert parse_csv_set({"a"}) == {"a"}
    assert parse_csv_set(None) is None
    assert parse_csv_set("") is None


def test_resolve_jobs_cli_wins():
    assert resolve_jobs(4, 2) == 4
    assert resolve_jobs(None, 2) == 2
    assert resolve_jobs(None, None) is None


def test_workers_for_print_handles_zero_and_none():
    assert workers_for_print(None) == 1
    assert workers_for_print(1) == 1
    assert workers_for_print(4) == 4
    # 0 → cpu_count(); just assert it's a positive int.
    assert workers_for_print(0) >= 1


def test_default_paths(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "project.toml").write_text(textwrap.dedent(f"""
        [rom]
        name = "demo"
        file = "{rom_path.name}"
        [rom.build]
    """))
    spec, spec_file = load_spec(tmp_path)
    assert default_output_path(spec, spec_file) == tmp_path / "demo.sfc"
    assert default_cache_dir(tmp_path) == tmp_path / ".cache"


@pytest.mark.parametrize("key", ["output_dir", "out-dir", "out_dir"])
def test_default_output_dir(tmp_path, key):
    """`[rom.build].output_dir` (and its aliases) redirects the default
    output path into that folder, relative to the spec file."""
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "project.toml").write_text(textwrap.dedent(f"""
        [rom]
        name = "demo"
        file = "{rom_path.name}"
        [rom.build]
        {key} = "out"
    """))
    spec, spec_file = load_spec(tmp_path)
    assert spec.output_dir == "out"
    assert default_output_path(spec, spec_file) == tmp_path / "out" / "demo.sfc"


def test_build_project_creates_output_dir(tmp_path):
    """build_project() honors output_dir end-to-end and creates the folder."""
    _project_with_rep(tmp_path)
    (tmp_path / "project.toml").write_text(
        (tmp_path / "project.toml").read_text().replace(
            "[rom.build]", '[rom.build]\noutput_dir = "out"', 1
        )
    )
    result = build_project(path=tmp_path, no_cache=True, no_progress=True,
                           print_summary=False)
    assert (tmp_path / "out" / "demo.sfc").exists()
    assert isinstance(result, BuildResult)


# ---- build_project --------------------------------------------------------

def _project_with_rep(tmp_path: Path) -> Path:
    """Build a minimal project.toml that does a single REP write at $600."""
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "patch.bin").write_bytes(b"\xCA\xFE\xBA\xBE")
    (tmp_path / "project.toml").write_text(textwrap.dedent(f"""
        [rom]
        name = "demo"
        file = "{rom_path.name}"

        [rom.build]
        pad = true

        [[rom.build.sections]]
        kind = "rep"
        offset = 0x600
        file = "patch.bin"
    """))
    return tmp_path


def test_build_project_returns_buildresult_and_writes_rom(tmp_path):
    proj = _project_with_rep(tmp_path)
    summary = io.StringIO()
    progress = io.StringIO()
    result = build_project(
        proj,
        no_cache=True,
        no_progress=True,
        print_summary=True,
        summary_stream=summary,
        progress_stream=progress,
    )
    assert isinstance(result, BuildResult)
    assert result.rom_path == proj / "demo.sfc"
    assert result.rom_path.exists()
    body = result.rom_path.read_bytes()
    assert body[0x600:0x604] == b"\xCA\xFE\xBA\xBE"
    # Summary block must look like the CLI's.
    out = summary.getvalue()
    assert "rom:" in out and "demo.sfc" in out
    assert "size:" in out
    assert "checksum:" in out
    assert "duration:" in out


def test_build_project_silenced(tmp_path):
    proj = _project_with_rep(tmp_path)
    summary = io.StringIO()
    result = build_project(
        proj, no_cache=True, no_progress=True,
        print_summary=False, summary_stream=summary,
    )
    assert isinstance(result, BuildResult)
    assert summary.getvalue() == ""


def test_build_project_only_filter_passes_through(tmp_path):
    proj = _project_with_rep(tmp_path)
    # Filter for a non-existent name → all sections skipped.
    result = build_project(
        proj, no_cache=True, no_progress=True, print_summary=False,
        only="nonexistent-section-name",
    )
    assert len(result.skipped) == 1


def test_build_project_defines_accepts_dict(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "patch_en.bin").write_bytes(b"EN!!")
    (tmp_path / "patch_jp.bin").write_bytes(b"JP!!")
    (tmp_path / "project.toml").write_text(textwrap.dedent(f"""
        [rom]
        name = "demo"
        file = "{rom_path.name}"
        [rom.build]
        pad = true
        [[rom.build.sections]]
        kind = "rep"
        offset = 0x600
        file = "patch_${{version}}.bin"
    """))
    out = build_project(
        tmp_path, no_cache=True, no_progress=True, print_summary=False,
        defines={"version": "jp"},
    )
    body = out.rom_path.read_bytes()
    assert body[0x600:0x604] == b"JP!!"


# ---- iter_step_builds -----------------------------------------------------

_ASCII_TBL = (
    "\n".join(f"{ord(c):02X}={c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ abcd")
    + "\n"
)


def test_iter_step_builds_yields_cumulative_results(tmp_path):
    plant = {
        0x100: b"AAA\x00", 0x108: b"BBB\x00", 0x110: b"CCC\x00",
        0x200: b"\x00\x81\x08\x81\x10\x81",
    }
    rom_path = _make_lorom(tmp_path, plant=plant)
    (tmp_path / "t.tbl").write_text(_ASCII_TBL, encoding="utf-8")
    (tmp_path / "s.txt").write_text(
        "<<$C000:0[$100]>>\nDDD\n"
        "<<$C000:1[$108]>>\nEEE\n"
        "<<$C000:2[$110]>>\nFFF\n",
        encoding="utf-8",
    )
    from retrotool.build import (
        BuildSpec, Section, SectionKind,
    )
    from pathlib import PurePosixPath
    spec = BuildSpec(
        sections=[Section(
            kind=SectionKind.SCRIPT,
            files=[PurePosixPath("s.txt")],
            table=PurePosixPath("t.tbl"),
            pointer_table=0x200, pointer_size=2, count=3,
            placement={"mode": "overflow"},
            attrs={"name": "dialog"},
            source="inline:dialog",
        )],
        freespace=[(0x10000, 0x20000)],
    )
    spec.original = PurePosixPath(rom_path.name)
    out = tmp_path / "out.sfc"
    results = list(iter_step_builds(
        spec,
        section=spec.sections[0],
        source_root=tmp_path,
        out_path=out,
        progress=1,
    ))
    # 3 entries → 3 steps, each writing a `.stepNNN.sfc`.
    assert len(results) == 3
    steps = [step for step, _, _, _ in results]
    assert steps == [1, 2, 3]
    paths = [p for _, _, _, p in results]
    assert paths[0].name == "out.step001.sfc"
    assert paths[2].name == "out.step003.sfc"
    # Step 1: only block 0 rewritten.
    body1 = paths[0].read_bytes()
    assert body1[0x100:0x103] == b"DDD"
    assert body1[0x108:0x10B] == b"BBB"  # untouched
    # Step 3: all three rewritten.
    body3 = paths[2].read_bytes()
    assert body3[0x100:0x103] == b"DDD"
    assert body3[0x108:0x10B] == b"EEE"
    assert body3[0x110:0x113] == b"FFF"


# ---- resolve_extract_dest -------------------------------------------------

def test_resolve_extract_dest_dest_wins(tmp_path):
    from retrotool.build import BuildSpec
    spec = BuildSpec()
    p, lang = resolve_extract_dest(spec, dest=tmp_path)
    assert p == tmp_path.resolve() and lang is None


def test_resolve_extract_dest_lang_splices_data_dir(tmp_path):
    from retrotool.build import BuildSpec
    spec = BuildSpec(data_dirs_by_lang={"jp": "jp_data"})
    p, lang = resolve_extract_dest(spec, lang="jp")
    assert p is None and lang == "jp"
    assert spec.en_data_dir == "jp_data"


def test_resolve_extract_dest_unknown_lang_lists_known(tmp_path):
    from retrotool.build import BuildSpec
    spec = BuildSpec(data_dirs_by_lang={"en": "en_data"})
    with pytest.raises(ValueError, match="known langs"):
        resolve_extract_dest(spec, lang="de")


def test_resolve_extract_dest_no_input_raises(tmp_path):
    from retrotool.build import BuildSpec
    spec = BuildSpec()
    with pytest.raises(ValueError, match="explicit destination"):
        resolve_extract_dest(spec)


def test_resolve_extract_dest_default_lang_from_spec(tmp_path):
    from retrotool.build import BuildSpec
    spec = BuildSpec(
        data_dirs_by_lang={"jp": "jp_data"},
        extract_config={"default_lang": "jp"},
    )
    p, lang = resolve_extract_dest(spec)
    assert p is None and lang == "jp"


# ---- migrate_project ------------------------------------------------------

def test_migrate_project_returns_text(tmp_path):
    f = tmp_path / "legacy.mbxml"
    f.write_text('<build original="rom.sfc"></build>')
    text = migrate_project(f)
    assert "<build" in text


def test_migrate_project_rejects_directory(tmp_path):
    with pytest.raises(ValueError):
        migrate_project(tmp_path)

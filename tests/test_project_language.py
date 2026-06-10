"""Tests for retrotool.project.language — `retrotool lang new` staging.

Builds a small fake translation project in tmp_path and verifies the
plan/apply model: script-folder copy, project.toml key edits, asset
forking + repointing, shared-patch handling, dry-run, and repair re-runs.
"""
import tomllib
from pathlib import Path

import pytest

from retrotool.project.language import (
    LanguageSetupError,
    apply_plan,
    build_language_plan,
    declared_languages,
    format_plan,
    split_lang_suffix,
)

PROJECT_TOML = '''\
# my translation project
en_data_dir = "data/en"
data_dirs = ["defs"]
build_lang = "en"

[rom]
name = "mygame_en"
file = "roms/mygame.sfc"
mapping = "lorom"

[rom.build]
output_dir = "out"

[[rom.build.sections]]
kind = "bin"
file = "fonts/mygame_en.bin"
offset = "0x100000"

[[rom.build.sections]]
kind = "graphics"
file = "art/logo.png"
offset = "0x110000"

[[rom.build.sections]]
kind = "asar"
file = "patches/engine.asm"
'''

DATADEF_TOML = '''\
[encoding]
table_file = "tables/mygame_en.tbl"
'''


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project.toml").write_text(PROJECT_TOML, encoding="utf-8")
    (tmp_path / "data" / "en").mkdir(parents=True)
    (tmp_path / "data" / "en" / "scenario_01.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "defs").mkdir()
    (tmp_path / "defs" / "dialog.toml").write_text(DATADEF_TOML, encoding="utf-8")
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "mygame_en.bin").write_bytes(b"\x01\x02")
    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "logo.png").write_bytes(b"\x89PNG")
    (tmp_path / "tables").mkdir()
    (tmp_path / "tables" / "mygame_en.tbl").write_text("41=A\n", encoding="utf-8")
    (tmp_path / "patches").mkdir()
    (tmp_path / "patches" / "engine.asm").write_text("; asm\n", encoding="utf-8")
    return tmp_path


def test_full_staging(project: Path):
    plan = build_language_plan(project, "en", "fr")
    apply_plan(plan)

    # Script folder copied
    assert (project / "data" / "fr" / "scenario_01.txt").read_text() == "hello"
    # Assets forked with the language suffix (stem _en re-suffixed; plain
    # stems get _fr appended)
    assert (project / "fonts" / "mygame_fr.bin").read_bytes() == b"\x01\x02"
    assert (project / "art" / "logo_fr.png").exists()
    assert (project / "tables" / "mygame_fr.tbl").exists()
    # Shared asar patch NOT forked
    assert not (project / "patches" / "engine_fr.asm").exists()

    cfg = tomllib.loads((project / "project.toml").read_text())
    assert cfg["fr_data_dir"] == "data/fr"
    assert cfg["build_lang"] == "fr"
    assert cfg["rom"]["name"] == "mygame_fr"
    files = [s["file"] for s in cfg["rom"]["build"]["sections"]]
    assert "fonts/mygame_fr.bin" in files
    assert "art/logo_fr.png" in files
    assert "patches/engine.asm" in files        # untouched
    # DataDef repointed
    sub = tomllib.loads((project / "defs" / "dialog.toml").read_text())
    assert sub["encoding"]["table_file"] == "tables/mygame_fr.tbl"
    # Comments preserved by the textual edit
    assert (project / "project.toml").read_text().startswith("# my translation project")


def test_dry_run_writes_nothing(project: Path):
    before = (project / "project.toml").read_text()
    plan = build_language_plan(project, "en", "fr")
    apply_plan(plan, dry_run=True)
    assert (project / "project.toml").read_text() == before
    assert not (project / "data" / "fr").exists()
    assert not (project / "fonts" / "mygame_fr.bin").exists()


def test_fork_all_includes_patches(project: Path):
    plan = build_language_plan(project, "en", "fr", fork_all=True)
    apply_plan(plan)
    assert (project / "patches" / "engine_fr.asm").exists()
    cfg = tomllib.loads((project / "project.toml").read_text())
    files = [s["file"] for s in cfg["rom"]["build"]["sections"]]
    assert "patches/engine_fr.asm" in files


def test_rerun_is_repair_noop(project: Path):
    apply_plan(build_language_plan(project, "en", "fr"))
    plan2 = build_language_plan(project, "en", "fr")
    # Everything already staged: no copies, no edits — only notes.
    assert plan2.empty, format_plan(project, plan2)


def test_source_rom_never_forked(project: Path):
    # Add a bin section that (mis)points at the source ROM — must be skipped.
    pt = project / "project.toml"
    pt.write_text(pt.read_text() + (
        '\n[[rom.build.sections]]\nkind = "bin"\nfile = "roms/mygame.sfc"\n'
    ), encoding="utf-8")
    (project / "roms").mkdir()
    (project / "roms" / "mygame.sfc").write_bytes(b"\x00" * 16)
    plan = build_language_plan(project, "en", "fr")
    assert all("roms/mygame" not in dst.as_posix() for _, dst, _ in plan.copies)


def test_missing_asset_noted_not_fatal(project: Path):
    (project / "art" / "logo.png").unlink()
    plan = build_language_plan(project, "en", "fr")
    assert any("SKIP art/logo.png" in n for n in plan.notes)
    apply_plan(plan)   # still applies the rest
    assert (project / "fonts" / "mygame_fr.bin").exists()


def test_validation_errors(project: Path):
    with pytest.raises(LanguageSetupError, match="lowercase"):
        build_language_plan(project, "en", "FR!")
    with pytest.raises(LanguageSetupError, match="differ"):
        build_language_plan(project, "en", "en")
    with pytest.raises(LanguageSetupError, match="available languages"):
        build_language_plan(project, "de", "fr")


def test_apply_aborts_if_file_changed_under_plan(project: Path):
    plan = build_language_plan(project, "en", "fr")
    # Simulate a concurrent edit that removes an expected anchor string.
    pt = project / "project.toml"
    pt.write_text(pt.read_text().replace('name = "mygame_en"', 'name = "x"'),
                  encoding="utf-8")
    with pytest.raises(LanguageSetupError, match="changed underneath"):
        apply_plan(plan)


def test_split_lang_suffix_longest_code_wins():
    langs = {"en", "pt", "br_pt"}
    assert split_lang_suffix("game_br_pt", langs) == ("game", "br_pt")
    assert split_lang_suffix("game_pt", langs) == ("game", "pt")
    assert split_lang_suffix("ascii", langs) == ("ascii", None)


def test_declared_languages():
    cfg = {"en_data_dir": "data/en", "br_pt_data_dir": "data/br_pt", "rom": {}}
    assert declared_languages(cfg) == ["br_pt", "en"]

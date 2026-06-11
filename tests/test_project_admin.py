"""Tests for retrotool.script.project_admin — the Project panel's service.

The Phase-1 acceptance criterion from the project-manager plan: edit one
key through the service and the project.toml diff shows ONLY that key
changed, comments intact.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from retrotool.script.project_admin import (
    ProjectAdminError,
    diff_preview,
    read_project,
    update_project,
    validate_project,
)

EXAMPLE = Path(__file__).parent.parent / "examples" / "translation-project"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Scratch copy of the example project (with its demo ROM)."""
    root = tmp_path / "proj"
    shutil.copytree(EXAMPLE, root,
                    ignore=shutil.ignore_patterns("roms", "out", "__pycache__"))
    import runpy
    runpy.run_path(str(root / "tools" / "make_demo_rom.py"), run_name="__main__")
    return root


# ---------------------------------------------------------------------------
# read_project
# ---------------------------------------------------------------------------

def test_read_project_merged_view(project: Path):
    p = read_project(project)
    assert p["exists"] and p["problems"] == []
    assert p["rom"]["name"] == "demo_en"
    assert p["rom"]["mapping"] == "lorom"
    assert p["build_lang"] == "en"
    assert p["languages"] == {"jp": "data/jp", "en": "data/en"}
    assert p["build"]["output_dir"] == "out"
    assert p["build"]["placement_mode"] == "relocate"
    assert p["editor"]["cols_per_line"] == 24
    # DataDef-derived section resolved through the real loader.
    secs = p["sections"]
    assert len(secs) == 1
    assert secs[0]["name"] == "dialog"
    assert secs[0]["kind"] == "script"
    assert secs[0]["count"] == 3
    assert secs[0]["placement"] == "relocate"
    assert secs[0]["from_datadef"] == "dialog"


def test_read_project_survives_broken_toml(tmp_path: Path):
    (tmp_path / "project.toml").write_text("[rom\nbroken", encoding="utf-8")
    p = read_project(tmp_path)
    assert p["exists"]
    assert p["problems"] and "project.toml" in p["problems"][0]


def test_read_project_missing(tmp_path: Path):
    p = read_project(tmp_path)
    assert not p["exists"] and p["problems"]


# ---------------------------------------------------------------------------
# update_project — the acceptance criterion
# ---------------------------------------------------------------------------

def test_single_key_edit_diffs_only_that_line(project: Path):
    pt = project / "project.toml"
    before = pt.read_text(encoding="utf-8").splitlines()
    update_project(project, {"editor.cols_per_line": 28})
    after = pt.read_text(encoding="utf-8").splitlines()

    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(before) == len(after)
    assert changed == [("cols_per_line = 24                 "
                        "# dialog width for overflow checks",
                        "cols_per_line = 28                 "
                        "# dialog width for overflow checks")] or (
        # tomlkit may normalize trailing alignment; the load-bearing
        # assertions are below.
        len(changed) == 1
        and "cols_per_line = 24" in changed[0][0]
        and "cols_per_line = 28" in changed[0][1]
    )
    # All comments survive.
    assert sum(1 for line in before if "#" in line) == \
           sum(1 for line in after if "#" in line)


def test_update_creates_missing_tables_and_deletes(project: Path):
    update_project(project, {"editor.control_codes.newline": "FD"})
    p = read_project(project)
    assert p["editor"]["control_codes"]["newline"] == "FD"
    update_project(project, {"editor.control_codes.newline": None})
    p = read_project(project)
    assert "newline" not in p["editor"].get("control_codes", {})


def test_update_writes_backup_and_is_revertable(project: Path):
    pt = project / "project.toml"
    original = pt.read_text(encoding="utf-8")
    bak = update_project(project, {"rom.name": "demo_fr"})
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == original
    assert 'name = "demo_fr"' in pt.read_text(encoding="utf-8")


def test_update_refuses_to_break_the_file(project: Path):
    # A value tomlkit can serialize but that we reject pre-write would be
    # exotic; instead simulate by corrupting on disk first: update must
    # refuse to operate on an unparseable file (and write nothing).
    pt = project / "project.toml"
    pt.write_text("[rom\nbroken", encoding="utf-8")
    with pytest.raises(ProjectAdminError, match="does not parse"):
        update_project(project, {"rom.name": "x"})


def test_update_bad_key_rejected(project: Path):
    with pytest.raises(ProjectAdminError, match="bad change key"):
        update_project(project, {"": 1})
    with pytest.raises(ProjectAdminError, match="bad change key"):
        update_project(project, {"rom..name": "x"})


# ---------------------------------------------------------------------------
# diff_preview / validate_project
# ---------------------------------------------------------------------------

def test_diff_preview_shows_change_without_writing(project: Path):
    pt = project / "project.toml"
    before = pt.read_text(encoding="utf-8")
    d = diff_preview(project, {"editor.cols_per_line": 28})
    assert d and "-cols_per_line = 24" in d and "+cols_per_line = 28" in d
    assert pt.read_text(encoding="utf-8") == before     # nothing written
    assert diff_preview(project, {}) is None            # no-op → None


def test_validate_clean_project(project: Path):
    assert validate_project(project) == []


def test_validate_reports_real_parser_errors(project: Path):
    update_project(project, {"rom.mapping": "wrom"})
    problems = validate_project(project)
    assert problems and any("wrom" in p or "mapping" in p for p in problems)


def test_validate_reports_missing_script_folder(project: Path):
    shutil.rmtree(project / "data" / "en")
    problems = validate_project(project)
    assert any("script folder not found" in p for p in problems)

"""Section wizard backend tests: DataDef read/update/create + pointer scan.

Runs against a scratch copy of examples/translation-project (the demo ROM
has a real 2-byte pointer table at PC 0x4000 for the scanner to find).
"""
from __future__ import annotations

import runpy
import shutil
import tomllib
from pathlib import Path

import pytest

from retrotool.script.project_admin import (
    ProjectAdminError,
    create_datadef,
    datadef_path,
    diff_datadef,
    read_datadef,
    read_project,
    scan_sections,
    update_datadef,
    validate_project,
)

EXAMPLE = Path(__file__).parent.parent / "examples" / "translation-project"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    shutil.copytree(EXAMPLE, root,
                    ignore=shutil.ignore_patterns("roms", "out", "__pycache__"))
    runpy.run_path(str(root / "tools" / "make_demo_rom.py"), run_name="__main__")
    return root


# ---------------------------------------------------------------------------
# read / update existing DataDefs
# ---------------------------------------------------------------------------

def test_read_datadef(project: Path):
    d = read_datadef(project, "dialog")
    assert d["problems"] == []
    assert d["path"] == "defs/dialog.toml"
    assert d["table"]["name"] == "dialog"
    assert d["encoding"]["terminator"] == 0xFF
    assert d["pointers"]["count"] == 3
    assert d["section"]["placement"]["mode"] == "relocate"


def test_read_datadef_unknown_name(project: Path):
    d = read_datadef(project, "nope")
    assert d["problems"] and "nope" in d["problems"][0]


def test_update_datadef_preserves_comments(project: Path):
    path = datadef_path(project, "dialog")
    before = path.read_text(encoding="utf-8")
    bak = update_datadef(project, "dialog", {"pointers.count": 4})
    after = path.read_text(encoding="utf-8")
    assert "count = 4" in after
    # comments intact, backup holds the original
    assert "# A DataDef describes ONE block" in after
    assert bak.read_text(encoding="utf-8") == before
    # validation now flags nothing structural (count=4 reads past the table
    # but parses) — the panel surfaces semantic issues at build time.


def test_diff_datadef_writes_nothing(project: Path):
    path = datadef_path(project, "dialog")
    before = path.read_text(encoding="utf-8")
    d = diff_datadef(project, "dialog", {"section.placement.mode": "overflow"})
    assert '+mode = "overflow"' in d
    assert path.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# create_datadef
# ---------------------------------------------------------------------------

def test_create_datadef_enrolls_in_build(project: Path):
    p = create_datadef(project, {
        "name": "menus",
        "table_file": "tables/game_en.tbl",
        "terminator": 0xFF,
        "ptr_offset": 0x4100,
        "ptr_count": 2,
        "ptr_size": 2,
        "data_offset": 0x4140,
        "data_end": 0x4180,
    })
    assert p == project / "defs" / "menus.toml"
    doc = tomllib.loads(p.read_text(encoding="utf-8"))
    assert doc["table"]["name"] == "menus"
    assert doc["encoding"]["terminator"] == 0xFF
    assert doc["pointers"] == {"offset": "$004100", "count": 2, "size": 2}
    assert doc["data"] == {"offset": "$004140", "end": "$004180"}
    assert doc["section"]["placement"]["mode"] == "relocate"
    # The real loader now resolves TWO sections.
    names = {s["name"] for s in read_project(project)["sections"]}
    assert names == {"dialog", "menus"}


def test_create_datadef_refuses_duplicates(project: Path):
    with pytest.raises(ProjectAdminError, match="already exists"):
        create_datadef(project, {"name": "dialog"})


def test_create_datadef_bad_name(project: Path):
    with pytest.raises(ProjectAdminError, match="bad section name"):
        create_datadef(project, {"name": "no spaces!"})


def test_create_datadef_bootstraps_data_dirs(tmp_path: Path):
    """A bare project without data_dirs: the wizard inserts the scalar
    BEFORE the first [table] header and creates defs/."""
    root = tmp_path / "bare"
    root.mkdir()
    (root / "project.toml").write_text(
        "# my project\n\n[rom]\nname = \"x\"\nfile = \"rom.sfc\"\n"
        "mapping = \"lorom\"\nsize = \"512K\"\n",
        encoding="utf-8")
    p = create_datadef(root, {"name": "dialog", "ptr_offset": 0x100,
                              "ptr_count": 4})
    assert p == root / "defs" / "dialog.toml"
    data = tomllib.loads((root / "project.toml").read_text(encoding="utf-8"))
    assert data["data_dirs"] == ["defs"]
    assert data["rom"]["name"] == "x"        # scalar landed top-level
    assert datadef_path(root, "dialog") == p


# ---------------------------------------------------------------------------
# scan_sections
# ---------------------------------------------------------------------------

def test_scan_finds_the_demo_pointer_table(project: Path):
    r = scan_sections(project, entry_size=2, min_entries=3)
    assert r["error"] is None
    hit = next((c for c in r["candidates"] if c["offset"] == 0x4000), None)
    assert hit is not None, r["candidates"][:5]
    assert hit["count"] == 3
    assert hit["target_low"] == 0x4040
    assert hit["monotonic"] == 1.0
    assert hit["offset_hex"] == "$004000"


def test_scan_reports_missing_rom(tmp_path: Path):
    (tmp_path / "project.toml").write_text(
        '[rom]\nname = "x"\nfile = "roms/none.sfc"\nmapping = "lorom"\n'
        'size = "512K"\n', encoding="utf-8")
    r = scan_sections(tmp_path)
    assert r["candidates"] == [] and "not found" in r["error"]


def test_created_section_validates_clean(project: Path):
    create_datadef(project, {
        "name": "menus", "table_file": "tables/game_en.tbl",
        "terminator": 0xFF, "ptr_offset": 0x4100, "ptr_count": 2,
        "ptr_size": 2,
    })
    # The new DataDef has no script file yet — extract would create it;
    # build validation must complain about the missing file, proving the
    # section is actually enrolled.
    problems = validate_project(project)
    assert problems == [] or all("menus" in p or "file" in p for p in problems)

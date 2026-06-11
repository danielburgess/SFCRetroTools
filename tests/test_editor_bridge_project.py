"""Bridge-level tests for the Project panel (headless — no pywebview).

Phase-1 acceptance: change a key through the panel's bridge path and the
editor reflects it immediately (hot reload), with project.toml diffing
only that key.
"""
from __future__ import annotations

import runpy
import shutil
from pathlib import Path

import pytest

from retrotool.script.editor_config import load_editor_config
from retrotool.script.script_editor import Bridge

EXAMPLE = Path(__file__).parent.parent / "examples" / "translation-project"


@pytest.fixture
def bridge(tmp_path: Path) -> Bridge:
    root = tmp_path / "proj"
    shutil.copytree(EXAMPLE, root,
                    ignore=shutil.ignore_patterns("roms", "out", "__pycache__"))
    runpy.run_path(str(root / "tools" / "make_demo_rom.py"), run_name="__main__")
    return Bridge(load_editor_config(root))


def test_get_project_via_bridge(bridge: Bridge):
    p = bridge.get_project()
    assert p["rom"]["name"] == "demo_en"
    assert p["sections"][0]["name"] == "dialog"
    assert p["problems"] == []


def test_save_project_hot_reloads_config(bridge: Bridge):
    # Before: 24 cols — no entry overflows.
    assert bridge.config.cols_per_line == 24
    assert all(not e["overflow"] for e in bridge.list_entries("dialog"))

    r = bridge.save_project({"editor.cols_per_line": 4})
    assert r["ok"] and r["error"] is None and r["problems"] == []
    assert Path(r["backup"]).exists()

    # After: config swapped in place, overflow checker uses the new width
    # (every demo line is wider than 4 half-cells).
    assert bridge.config.cols_per_line == 4
    entries = bridge.list_entries("dialog")
    assert entries and all(e["overflow"] for e in entries)
    # And the file on disk says 4 — comments intact.
    text = (bridge.config.root / "project.toml").read_text(encoding="utf-8")
    assert "cols_per_line = 4" in text
    # Comments survive the tomlkit round-trip.
    assert "# Per-language script roots." in text
    assert "# overflow checker should enforce" in text


def test_save_project_surfaces_validation_problems(bridge: Bridge):
    r = bridge.save_project({"rom.mapping": "wrom"})
    assert r["ok"]                       # write succeeded (file still parses)
    assert any("wrom" in p or "mapping" in p for p in r["problems"])


def test_preview_project_changes_writes_nothing(bridge: Bridge):
    pt = bridge.config.root / "project.toml"
    before = pt.read_text(encoding="utf-8")
    r = bridge.preview_project_changes({"editor.cols_per_line": 30})
    assert r["error"] is None
    assert "+cols_per_line = 30" in r["diff"]
    assert pt.read_text(encoding="utf-8") == before


def test_reload_project_flushes_pending_saves(bridge: Bridge):
    # Queue an edit, then reload — the flush must land it on disk first.
    bridge.queue_save("dialog", 0, "Changed![FF]")
    bridge.reload_project()
    assert bridge.scenarios["dialog"].entries[0]["body"] == "Changed![FF]"


# ---------------------------------------------------------------------------
# Section wizard via the bridge
# ---------------------------------------------------------------------------

def test_wizard_scan_edit_create_roundtrip(bridge: Bridge):
    # Scan seeds the wizard with the demo table.
    r = bridge.scan_pointers(entry_size=2, min_entries=3)
    assert r["error"] is None
    assert any(c["offset"] == 0x4000 for c in r["candidates"])

    # Edit the existing DataDef through the panel path.
    d = bridge.get_datadef("dialog")
    assert d["path"] == "defs/dialog.toml"
    pv = bridge.preview_datadef("dialog", {"section.placement.mode": "overflow"})
    assert '+mode = "overflow"' in pv["diff"]
    res = bridge.save_datadef("dialog", {"pointers.count": 3})  # no-op value
    assert res["ok"] and res["problems"] == []

    # Create a second section; the hot-reloaded project lists both.
    res = bridge.create_section({
        "name": "menus", "table_file": "tables/game_en.tbl",
        "terminator": 0xFF, "ptr_offset": 0x4100, "ptr_count": 2,
        "ptr_size": 2,
    })
    assert res["ok"], res
    assert res["path"] == "defs/menus.toml"
    names = {s["name"] for s in bridge.get_project()["sections"]}
    assert names == {"dialog", "menus"}


def test_wizard_create_duplicate_fails_cleanly(bridge: Bridge):
    res = bridge.create_section({"name": "dialog"})
    assert not res["ok"] and "already exists" in res["error"]

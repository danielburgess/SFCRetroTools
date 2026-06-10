"""Golden test: docs/project-toml-reference.md stays in sync with the code.

Two directions:

1. Code -> doc sweep: every section-attrs / overflow-config key literal the
   build pipeline reads (``attrs.get("...")`` and its local aliases) must
   appear in the reference doc. Adding a new handler attribute without
   documenting it fails here.
2. Curated contract: a snapshot of the keys each subsystem is known to
   consume must each appear in the doc. If a key is intentionally removed
   from the code, delete it from the doc AND from this list.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOC = (ROOT / "docs" / "project-toml-reference.md").read_text(encoding="utf-8")

HANDLERS = (ROOT / "retrotool" / "build" / "handlers.py").read_text(encoding="utf-8")
OVERFLOW = (ROOT / "retrotool" / "build" / "overflow.py").read_text(encoding="utf-8")
DRIVER = (ROOT / "retrotool" / "build" / "driver.py").read_text(encoding="utf-8")

# Local aliases for `section.attrs` used inside handlers/driver:
#   attrs.get("x") / a.get("x") / raw.get("x") / _attr_hex(a, "x")
_ATTR_RE = re.compile(r"""(?:\battrs|\ba|\braw)\.get\(\s*["']([a-z][a-z0-9_-]*)["']"""
                      r"""|_attr_hex\(\s*\w+,\s*["']([a-z][a-z0-9_-]*)["']""")
# Overflow strategy config reads: cfg.get("x") / raw.get("x") in overflow.py.
_CFG_RE = re.compile(r"""\b(?:cfg|raw)\.get\(\s*["']([a-z][a-z0-9_-]*)["']""")

# Dict-access strings that are NOT user-facing config keys (internal plumbing,
# runtime context, kwargs bags). Keep small and explicit.
_NOT_CONFIG = {
    "mode",          # placement.mode — documented prose-side, asserted below
    "default",       # subcode default sentinel
}


def _swept_attr_keys() -> set[str]:
    keys: set[str] = set()
    for src in (HANDLERS, DRIVER):
        for m in _ATTR_RE.finditer(src):
            keys.add(m.group(1) or m.group(2))
    return keys - _NOT_CONFIG


def _swept_overflow_keys() -> set[str]:
    return {m.group(1) for m in _CFG_RE.finditer(OVERFLOW)} - _NOT_CONFIG


def test_sweep_is_not_vacuous():
    """Guard the regexes themselves: if a refactor renames the attrs
    aliases, the sweep must fail loudly instead of passing empty."""
    assert len(_swept_attr_keys()) >= 25
    assert len(_swept_overflow_keys()) >= 7


def test_swept_handler_attrs_are_documented():
    missing = {k for k in _swept_attr_keys() if f"`{k}`" not in DOC}
    assert not missing, (
        f"section attrs consumed in code but absent from "
        f"docs/project-toml-reference.md: {sorted(missing)}"
    )


def test_swept_overflow_config_is_documented():
    missing = {k for k in _swept_overflow_keys() if f"`{k}`" not in DOC}
    assert not missing, (
        f"overflow config keys consumed in code but absent from "
        f"docs/project-toml-reference.md: {sorted(missing)}"
    )


# Snapshot of the authored-key contract (2026-06-10). These are keys users
# write in project.toml / DataDef tomls; each must stay documented.
CURATED = [
    # top-level / [rom] / [rom.build]
    "data_dirs", "build_lang", "mapping", "freespace", "output_dir",
    "include", "order", "revbyteloc", "jobs", "diff", "pad-byte",
    # sections core
    "kind", "offset", "codec", "grow", "pointer-table", "pointer-size",
    "count", "stride", "terminator", "fallback-table", "dedupe",
    "textbuf-limit", "pad-to", "cache", "export-label", "alias",
    # graphics
    "bpp", "colors", "palettes", "no-flip", "color-zero",
    "palette-from-png", "palette-anchors", "map-offset", "map-cols",
    "map-entries", "map-base-entry", "tile-base", "tile-count",
    "priority", "encoder",
    # assemblers / python / libsfx
    "includes", "defines", "allow-shrink", "constants", "strict",
    "bass-cmd", "config", "lib-paths", "cfg-paths", "cpu", "debug",
    "sym", "length", "allow-truncate", "module", "func", "stack-size",
    # overflow strategy
    "strategy", "marker", "pointer-encoder", "return-pointer-encoder",
    "splitter", "splitter-arg", "redirect-back", "defer-pointer",
    "undersized", "slot-measure",
    # word-wrap
    "line-width", "max-lines", "wrap-mode", "fill-char",
    # extract / mesen
    "default_lang", "sync-sram", "saves-dir", "archive-overwritten",
    # DataDef
    "table_file", "fallback", "bank_override", "compression",
    "compression_params", "target", "en_file", "clobber_lead_entries",
    "block_len", "ptr_writes",
]


def test_curated_keys_are_documented():
    missing = [k for k in CURATED if f"`{k}`" not in DOC]
    assert not missing, (
        f"keys missing from docs/project-toml-reference.md: {missing}"
    )


def test_doc_states_the_pc_offset_convention():
    """The addressing convention is the doc's load-bearing claim — it must
    survive edits (see the 2026-06-10 pointers.offset extract/build bug)."""
    assert "PC file offset" in DOC
    assert "placement" in DOC and "relocate" in DOC and "overflow" in DOC


def test_readme_links_the_reference():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/project-toml-reference.md" in readme, (
        "README must link the project.toml reference doc"
    )

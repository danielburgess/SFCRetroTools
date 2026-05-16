"""Transitive include/insert dependency scanner for bass v18 cache keys.

Mirrors test_asar_deps but exercises bass-flavored directives:
`include "file.asm"` (source — recursed) and
`insert [name, ] "file.bin"[, offset[, length]]` (binary — hashed only).
"""
from __future__ import annotations

from pathlib import Path

from retrotool.build.asar_deps import scan_bass_deps


def test_bass_scan_finds_direct_include(tmp_path):
    (tmp_path / "a.asm").write_text('include "b.asm"\n')
    (tmp_path / "b.asm").write_text("db $42\n")
    deps = scan_bass_deps(tmp_path / "a.asm")
    names = [d.name for d in deps]
    assert names == ["a.asm", "b.asm"]


def test_bass_scan_recurses_transitively(tmp_path):
    (tmp_path / "a.asm").write_text('include "b.asm"\n')
    (tmp_path / "b.asm").write_text('include "c.asm"\n')
    (tmp_path / "c.asm").write_text("nop\n")
    names = [d.name for d in scan_bass_deps(tmp_path / "a.asm")]
    assert names[0] == "a.asm"
    assert set(names[1:]) == {"b.asm", "c.asm"}


def test_bass_scan_handles_insert_without_recursing(tmp_path):
    (tmp_path / "a.asm").write_text('insert "data.bin"\n')
    (tmp_path / "data.bin").write_bytes(b"\xDE\xAD")
    deps = scan_bass_deps(tmp_path / "a.asm")
    names = [d.name for d in deps]
    assert set(names) == {"a.asm", "data.bin"}


def test_bass_scan_handles_named_insert(tmp_path):
    """`insert tag, "file.bin"` — bass form for naming the inserted blob.
    The leading identifier must not derail path extraction."""
    (tmp_path / "a.asm").write_text('insert sample, "data.bin"\n')
    (tmp_path / "data.bin").write_bytes(b"\xCA\xFE")
    deps = scan_bass_deps(tmp_path / "a.asm")
    names = [d.name for d in deps]
    assert set(names) == {"a.asm", "data.bin"}


def test_bass_scan_handles_insert_with_offset_length(tmp_path):
    """`insert "file.bin", $100, $20` — offset+length suffix must not
    derail the path match."""
    (tmp_path / "a.asm").write_text('insert "data.bin", $100, $20\n')
    (tmp_path / "data.bin").write_bytes(b"\x00" * 0x200)
    deps = scan_bass_deps(tmp_path / "a.asm")
    names = [d.name for d in deps]
    assert set(names) == {"a.asm", "data.bin"}


def test_bass_scan_respects_include_dirs(tmp_path):
    (tmp_path / "a.asm").write_text('include "shared.asm"\n')
    subdir = tmp_path / "libs"
    subdir.mkdir()
    (subdir / "shared.asm").write_text("nop\n")
    deps = scan_bass_deps(tmp_path / "a.asm", include_dirs=[subdir])
    names = [d.name for d in deps]
    assert set(names) == {"a.asm", "shared.asm"}


def test_bass_scan_ignores_commented_includes(tmp_path):
    (tmp_path / "a.asm").write_text(
        '// include "ghost1.asm"\n'
        '; include "ghost2.asm"\n'
        '/* include "ghost3.asm" */\n'
        'include "real.asm"\n'
    )
    (tmp_path / "real.asm").write_text("nop\n")
    names = [d.name for d in scan_bass_deps(tmp_path / "a.asm")]
    assert set(names) == {"a.asm", "real.asm"}


def test_bass_scan_does_not_match_asar_incsrc(tmp_path):
    """Sanity guard: bass scanner should NOT pick up `incsrc` directives.
    If a project mixes dialects (rare but possible during migration), the
    bass scanner stops at bass-flavored directives only."""
    (tmp_path / "a.asm").write_text('incsrc "asar_only.asm"\n')
    (tmp_path / "asar_only.asm").write_text("nop\n")
    deps = scan_bass_deps(tmp_path / "a.asm")
    names = [d.name for d in deps]
    assert names == ["a.asm"]

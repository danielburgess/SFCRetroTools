"""Transitive incsrc/incbin dependency scanner for ASAR cache keys."""
from __future__ import annotations

from pathlib import Path

from retrotool.build.asar_deps import scan_deps


def test_scan_finds_direct_incsrc(tmp_path):
    (tmp_path / "a.asm").write_text('incsrc "b.asm"\n')
    (tmp_path / "b.asm").write_text("db $42\n")
    deps = scan_deps(tmp_path / "a.asm")
    names = [d.name for d in deps]
    assert names == ["a.asm", "b.asm"]


def test_scan_recurses_transitively(tmp_path):
    (tmp_path / "a.asm").write_text('incsrc "b.asm"\n')
    (tmp_path / "b.asm").write_text('incsrc "c.asm"\n')
    (tmp_path / "c.asm").write_text("nop\n")
    names = [d.name for d in scan_deps(tmp_path / "a.asm")]
    assert names[0] == "a.asm"
    assert set(names[1:]) == {"b.asm", "c.asm"}


def test_scan_handles_incbin_without_recursing(tmp_path):
    (tmp_path / "a.asm").write_text('incbin "data.bin"\n')
    (tmp_path / "data.bin").write_bytes(b"\xDE\xAD")
    deps = scan_deps(tmp_path / "a.asm")
    names = [d.name for d in deps]
    assert set(names) == {"a.asm", "data.bin"}


def test_scan_respects_include_dirs(tmp_path):
    (tmp_path / "a.asm").write_text('incsrc "shared.asm"\n')
    subdir = tmp_path / "libs"
    subdir.mkdir()
    (subdir / "shared.asm").write_text("nop\n")
    deps = scan_deps(tmp_path / "a.asm", include_dirs=[subdir])
    names = [d.name for d in deps]
    assert set(names) == {"a.asm", "shared.asm"}


def test_scan_missing_include_silently_skipped(tmp_path):
    (tmp_path / "a.asm").write_text('incsrc "does_not_exist.asm"\n')
    names = [d.name for d in scan_deps(tmp_path / "a.asm")]
    assert names == ["a.asm"]


def test_scan_ignores_commented_includes(tmp_path):
    (tmp_path / "a.asm").write_text(
        "; incsrc \"commented.asm\"\n"
        "// incsrc \"also_commented.asm\"\n"
        '/* incsrc "block.asm" */\n'
        'incsrc "real.asm"\n'
    )
    (tmp_path / "commented.asm").write_text("nop\n")
    (tmp_path / "also_commented.asm").write_text("nop\n")
    (tmp_path / "block.asm").write_text("nop\n")
    (tmp_path / "real.asm").write_text("nop\n")
    names = [d.name for d in scan_deps(tmp_path / "a.asm")]
    assert set(names) == {"a.asm", "real.asm"}


def test_scan_cycle_safe(tmp_path):
    (tmp_path / "a.asm").write_text('incsrc "b.asm"\n')
    (tmp_path / "b.asm").write_text('incsrc "a.asm"\n')
    names = [d.name for d in scan_deps(tmp_path / "a.asm")]
    assert set(names) == {"a.asm", "b.asm"}


def test_scan_unquoted_path(tmp_path):
    (tmp_path / "a.asm").write_text("incsrc b.asm\n")
    (tmp_path / "b.asm").write_text("nop\n")
    names = [d.name for d in scan_deps(tmp_path / "a.asm")]
    assert set(names) == {"a.asm", "b.asm"}


def test_scan_incbin_with_range_suffix(tmp_path):
    (tmp_path / "a.asm").write_text('incbin "data.bin":0-16\n')
    (tmp_path / "data.bin").write_bytes(b"\x00" * 32)
    names = [d.name for d in scan_deps(tmp_path / "a.asm")]
    assert set(names) == {"a.asm", "data.bin"}

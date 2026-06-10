"""Golden test: examples/translation-project stays runnable.

The quickstart example is documentation-as-code — if the build pipeline or
the config schema drifts, this test fails before a user does. It copies the
example into tmp_path, generates the demo ROM with the shipped generator,
builds, and verifies the inserted English script + rewritten pointer table.
It also re-extracts the Japanese script and compares it against the shipped
data/jp/dialog.txt reference dump.
"""
from __future__ import annotations

import runpy
import shutil
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).parent.parent / "examples" / "translation-project"

PTR_TABLE_PC = 0x4000
PTR_SIZE = 2
COUNT = 3


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A scratch copy of the example with the demo ROM generated."""
    root = tmp_path / "translation-project"
    shutil.copytree(EXAMPLE, root,
                    ignore=shutil.ignore_patterns("roms", "out", ".venv",
                                                  "__pycache__"))
    # Run the shipped generator exactly as the README instructs.
    runpy.run_path(str(root / "tools" / "make_demo_rom.py"), run_name="__main__")
    rom = root / "roms" / "game.sfc"
    assert rom.exists() and rom.stat().st_size == 0x80_000
    return root


def _pointers(rom: bytes) -> list[int]:
    return [
        int.from_bytes(rom[PTR_TABLE_PC + i * PTR_SIZE:
                           PTR_TABLE_PC + (i + 1) * PTR_SIZE], "little")
        for i in range(COUNT)
    ]


def _ptr_to_pc(ptr: int, table_pc: int = PTR_TABLE_PC) -> int:
    """16-bit within-bank LoROM pointer -> PC, bank implied by the table."""
    bank_base = table_pc & ~0x7FFF
    return bank_base | (ptr & 0x7FFF)


def test_demo_rom_contains_jp_script(project: Path):
    rom = (project / "roms" / "game.sfc").read_bytes()
    pcs = [_ptr_to_pc(p) for p in _pointers(rom)]
    # Entry 0: こんにちは [FD] せかい ! FF per tables/game_jp.tbl
    e0 = rom[pcs[0]:pcs[0] + 11]
    assert e0 == bytes([0x80, 0x81, 0x82, 0x83, 0x84, 0xFD,
                        0x85, 0x86, 0x87, 0x21, 0xFF])


def test_build_inserts_english_script(project: Path):
    from retrotool.build import build_project
    build_project(project, no_progress=True)
    out = project / "out" / "demo_en.sfc"
    assert out.exists()
    rom = out.read_bytes()
    pcs = [_ptr_to_pc(p) for p in _pointers(rom)]

    def entry(pc: int) -> bytes:
        end = rom.index(0xFF, pc)
        return rom[pc:end]

    assert entry(pcs[0]) == b"Hello,\xFDworld!"
    assert entry(pcs[1]) == b"Fight!"
    assert entry(pcs[2]) == b"The End"
    # Relocate mode leaves the original JP bytes untouched.
    assert rom[0x4040:0x4045] == bytes([0x80, 0x81, 0x82, 0x83, 0x84])


def test_extract_jp_matches_shipped_reference(project: Path):
    """The README's 'how data/jp was made' step reproduces the shipped dump."""
    from retrotool.build import extract_project
    dd = project / "defs" / "dialog.toml"
    dd.write_text(
        dd.read_text(encoding="utf-8").replace(
            'table_file = "tables/game_en.tbl"',
            'table_file = "tables/game_jp.tbl"'),
        encoding="utf-8",
    )
    # Re-extract into the copy and compare with the reference shipped in git.
    extract_project(project, lang="jp", assume_yes=True)
    got = (project / "data" / "jp" / "dialog.txt").read_text(encoding="utf-16")
    want = (EXAMPLE / "data" / "jp" / "dialog.txt").read_text(encoding="utf-16")
    assert got == want
    assert "こんにちは[FD]せかい!" in got


def test_editor_config_loads_for_example(project: Path):
    """`retrotool edit .` on the example resolves dirs/tables with no
    [editor.preview] — i.e. text-only mode, EN editable, JP reference."""
    from retrotool.script.editor_config import load_editor_config
    cfg = load_editor_config(project)
    assert cfg.lang == "en"
    assert cfg.data_dir == (project / "data" / "en").resolve()
    assert cfg.reference_dir == (project / "data" / "jp").resolve()
    assert cfg.table.name == "game_en.tbl"
    assert cfg.reference_table.name == "game_jp.tbl"
    assert not cfg.preview.enabled
    assert cfg.project_name == "demo_en"

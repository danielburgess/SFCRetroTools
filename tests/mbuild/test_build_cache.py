"""Phase 5.6 — BuildCache hookup."""
from __future__ import annotations

from pathlib import Path

from retrotool.core.cache import BuildCache
from retrotool.mbuild import BuildSpec, Section, SectionKind, build
from tests.mbuild.test_build import _make_lorom


def _spec(tmp_path: Path, *, init_file: bool = True) -> BuildSpec:
    if init_file:
        (tmp_path / "patch.bin").write_bytes(b"\xAA\xBB\xCC\xDD")
    return BuildSpec(
        sections=[Section(
            kind=SectionKind.REP, offset=0x100,
            files=[Path("patch.bin")],
            attrs={"file": "patch.bin", "offset": "100"},
            source="test",
        )],
    )


def test_first_build_misses_then_second_hits(tmp_path):
    rom_path = _make_lorom(tmp_path)
    cache = BuildCache(tmp_path / ".cache")

    out1 = tmp_path / "out1.sfc"
    r1 = build(_spec(tmp_path), source_root=tmp_path, out_path=out1,
               original_rom=rom_path, cache=cache)
    assert r1.cache_hits == 0
    assert r1.sections[0].cache_hit is False

    out2 = tmp_path / "out2.sfc"
    r2 = build(_spec(tmp_path), source_root=tmp_path, out_path=out2,
               original_rom=rom_path, cache=cache)
    assert r2.cache_hits == 1
    assert r2.sections[0].cache_hit is True
    # Same payload regardless of cache path.
    assert out1.read_bytes() == out2.read_bytes()


def test_cache_invalidates_when_input_changes(tmp_path):
    rom_path = _make_lorom(tmp_path)
    cache = BuildCache(tmp_path / ".cache")

    build(_spec(tmp_path), source_root=tmp_path, out_path=tmp_path / "a.sfc",
          original_rom=rom_path, cache=cache)
    # Mutate the input file → key changes → must miss.
    (tmp_path / "patch.bin").write_bytes(b"\x11\x22\x33\x44")
    r2 = build(_spec(tmp_path, init_file=False), source_root=tmp_path,
               out_path=tmp_path / "b.sfc", original_rom=rom_path, cache=cache)
    assert r2.cache_hits == 0
    assert (tmp_path / "b.sfc").read_bytes()[0x100:0x104] == b"\x11\x22\x33\x44"


def test_no_cache_means_no_hits(tmp_path):
    rom_path = _make_lorom(tmp_path)
    r = build(_spec(tmp_path), source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path)
    assert r.cache_hits == 0

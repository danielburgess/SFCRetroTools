"""Phase 5.6 — BuildCache hookup."""
from __future__ import annotations

from pathlib import Path

from retrotool.core.cache import BuildCache
from retrotool.mbuild import BuildSpec, Section, SectionKind, build
from tests.mbuild.conftest import _make_lorom


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


def test_cache_distinguishes_sections_by_size(tmp_path):
    """H7 regression — `size=` is a first-class Section field, not always in
    `attrs` after coercion. Two sections that differ only on `size` must NOT
    collide in the cache."""
    rom_path = _make_lorom(tmp_path)
    cache = BuildCache(tmp_path / ".cache")
    (tmp_path / "patch.bin").write_bytes(b"\xAA\xBB")

    # Build A: size=2 (exact length).
    spec_a = BuildSpec(sections=[Section(
        kind=SectionKind.BIN, offset=0x100, files=[Path("patch.bin")],
        size=2, attrs={"file": "patch.bin", "offset": "100"}, source="a",
    )])
    build(spec_a, source_root=tmp_path, out_path=tmp_path / "a.sfc",
          original_rom=rom_path, cache=cache)

    # Build B: size=8 (zero-padded). Must miss A's cached 2-byte blob; the v1
    # cache key would have collided since `size` was not hashed.
    spec_b = BuildSpec(sections=[Section(
        kind=SectionKind.BIN, offset=0x100, files=[Path("patch.bin")],
        size=8, attrs={"file": "patch.bin", "offset": "100"}, source="b",
    )])
    rb = build(spec_b, source_root=tmp_path, out_path=tmp_path / "b.sfc",
               original_rom=rom_path, cache=cache)
    assert rb.cache_hits == 0
    body_b = (tmp_path / "b.sfc").read_bytes()
    assert body_b[0x100:0x108] == b"\xAA\xBB\x00\x00\x00\x00\x00\x00"


def test_no_cache_means_no_hits(tmp_path):
    rom_path = _make_lorom(tmp_path)
    r = build(_spec(tmp_path), source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path)
    assert r.cache_hits == 0


def test_multi_range_writes_frame_roundtrip(tmp_path):
    """Handlers may return list[WriteRange]; cache must frame+replay every range."""
    from retrotool.mbuild.build import _pack_writes, _unpack_writes
    from retrotool.mbuild.handlers import WriteRange
    rom = bytearray(0x200)
    rom[0x100:0x104] = b"\xAA\xBB\xCC\xDD"
    rom[0x180:0x183] = b"\x11\x22\x33"
    writes = [WriteRange(0x100, 4), WriteRange(0x180, 3)]
    blob = _pack_writes(bytes(rom), writes)
    unpacked = _unpack_writes(blob)
    assert unpacked == [(0x100, b"\xAA\xBB\xCC\xDD"), (0x180, b"\x11\x22\x33")]


def test_legacy_cache_artifact_rejected_cleanly(tmp_path):
    """Raw blobs (pre-v3 format) must raise a clear ValueError, not silently misread."""
    from retrotool.mbuild.build import _unpack_writes
    import pytest as _pt
    with _pt.raises(ValueError, match="magic"):
        _unpack_writes(b"\xAA\xBB\xCC\xDD")

"""Phase 5.6 — BuildCache hookup."""
from __future__ import annotations

from pathlib import Path

import pytest

from retrotool.core.cache import BuildCache
from retrotool.build import BuildSpec, Section, SectionKind, build
from retrotool.build.handlers import WriteRange
from tests.build.conftest import _make_lorom


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
    from retrotool.build.driver import _pack_writes, _unpack_writes
    from retrotool.build.handlers import WriteRange
    rom = bytearray(0x200)
    rom[0x100:0x104] = b"\xAA\xBB\xCC\xDD"
    rom[0x180:0x183] = b"\x11\x22\x33"
    writes = [WriteRange(0x100, 4), WriteRange(0x180, 3)]
    blob = _pack_writes(bytes(rom), writes)
    unpacked = _unpack_writes(blob)
    assert unpacked == [(0x100, b"\xAA\xBB\xCC\xDD"), (0x180, b"\x11\x22\x33")]


def test_legacy_cache_artifact_rejected_cleanly(tmp_path):
    """Raw blobs (pre-v3 format) must raise a clear ValueError, not silently misread."""
    from retrotool.build.driver import _unpack_writes
    import pytest as _pt
    with _pt.raises(ValueError, match="magic"):
        _unpack_writes(b"\xAA\xBB\xCC\xDD")


# ---- opt-in cache overrides (cache="1" / cache="0") ---------------------


def test_cache_override_false_skips_cacheable_kind(tmp_path):
    """cache=False forces cache-miss on a kind that would otherwise cache."""
    rom_path = _make_lorom(tmp_path)
    cache = BuildCache(tmp_path / ".cache")
    (tmp_path / "p.bin").write_bytes(b"\xAA\xBB\xCC\xDD")

    def _spec_nocache():
        return BuildSpec(sections=[Section(
            kind=SectionKind.REP, offset=0x100,
            files=[Path("p.bin")], cache=False,
            attrs={"file": "p.bin", "offset": "100"}, source="test",
        )])

    r1 = build(_spec_nocache(), source_root=tmp_path, out_path=tmp_path / "a.sfc",
               original_rom=rom_path, cache=cache)
    r2 = build(_spec_nocache(), source_root=tmp_path, out_path=tmp_path / "b.sfc",
               original_rom=rom_path, cache=cache)
    # Second build would hit if cache=False were ignored.
    assert r1.cache_hits == 0
    assert r2.cache_hits == 0


def test_cache_override_true_enables_asar_and_caches_diff(tmp_path, monkeypatch):
    """cache=True on an ASAR section enables the opt-in path: diff-mode writes
    get stored + a second build replays the diff without re-invoking asar."""
    rom_path = _make_lorom(tmp_path)
    cache = BuildCache(tmp_path / ".cache")
    (tmp_path / "p.asm").write_text("org $8000\ndb $AA\n")

    call_count = {"n": 0}

    class _Result:
        ok = True
        log = ""

    def _fake_apply(rom_in, patch, rom_out):
        call_count["n"] += 1
        out = bytearray(rom_in.read_bytes())
        out[0x200] = 0xAA
        out[0x201] = 0xBB
        rom_out.write_bytes(bytes(out))
        return _Result()

    monkeypatch.setattr("retrotool.asm.patcher.apply_patch", _fake_apply)

    def _asar_spec():
        return BuildSpec(sections=[Section(
            kind=SectionKind.ASAR, files=[Path("p.asm")], cache=True,
            attrs={"file": "p.asm"}, source="t",
        )])

    r1 = build(_asar_spec(), source_root=tmp_path, out_path=tmp_path / "a.sfc",
               original_rom=rom_path, cache=cache)
    assert r1.cache_hits == 0
    assert call_count["n"] == 1

    r2 = build(_asar_spec(), source_root=tmp_path, out_path=tmp_path / "b.sfc",
               original_rom=rom_path, cache=cache)
    assert r2.cache_hits == 1
    # asar must NOT have run on the cached build.
    assert call_count["n"] == 1
    # Output bytes must match.
    assert (tmp_path / "a.sfc").read_bytes() == (tmp_path / "b.sfc").read_bytes()


def test_asar_cache_invalidates_on_incsrc_change(tmp_path, monkeypatch):
    """Editing an incsrc'd file bumps the cache key and forces re-asar."""
    rom_path = _make_lorom(tmp_path)
    cache = BuildCache(tmp_path / ".cache")
    (tmp_path / "p.asm").write_text('incsrc "helper.asm"\n')
    (tmp_path / "helper.asm").write_text("org $8000\ndb $AA\n")

    call_count = {"n": 0}

    class _Result:
        ok = True
        log = ""

    def _fake_apply(rom_in, patch, rom_out):
        call_count["n"] += 1
        # Simulate the patch reading helper.asm would emit different bytes.
        helper = (tmp_path / "helper.asm").read_text()
        out = bytearray(rom_in.read_bytes())
        out[0x200] = 0xAA if "AA" in helper else 0xEE
        rom_out.write_bytes(bytes(out))
        return _Result()

    monkeypatch.setattr("retrotool.asm.patcher.apply_patch", _fake_apply)

    def _asar_spec():
        return BuildSpec(sections=[Section(
            kind=SectionKind.ASAR, files=[Path("p.asm")], cache=True,
            attrs={"file": "p.asm"}, source="t",
        )])

    build(_asar_spec(), source_root=tmp_path, out_path=tmp_path / "a.sfc",
          original_rom=rom_path, cache=cache)
    assert call_count["n"] == 1

    # Mutate the *included* file (not the entry) — must invalidate.
    (tmp_path / "helper.asm").write_text("org $8000\ndb $EE\n")
    r = build(_asar_spec(), source_root=tmp_path, out_path=tmp_path / "b.sfc",
              original_rom=rom_path, cache=cache)
    assert r.cache_hits == 0
    assert call_count["n"] == 2
    assert (tmp_path / "b.sfc").read_bytes()[0x200] == 0xEE

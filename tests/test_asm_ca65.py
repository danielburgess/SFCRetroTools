"""Tests for retrotool.asm.ca65 — ca65/ld65 wrappers with BuildCache integration."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from retrotool.asm.ca65 import (
    Ca65Assembler,
    Ca65Error,
    Ld65Error,
    Ld65Linker,
)
from retrotool.core.cache import BuildCache


_HAS_LIBSFX = False
try:
    import retrotool_libsfx  # noqa: F401
    _HAS_LIBSFX = True
except ImportError:
    pass


libsfx = pytest.mark.skipif(not _HAS_LIBSFX, reason="retrotool_libsfx not installed")


# Minimal ld65 config — 32KB LoROM, single CODE segment at $008000.
_MINIMAL_CFG = dedent("""
    MEMORY {
        ROM: start = $008000, size = $8000, fill = yes, fillval = $ff, file = %O;
    }
    SEGMENTS {
        CODE: load = ROM, type = ro;
    }
""").strip()


def _write_stub_asm(dest: Path, body: str | None = None) -> Path:
    """Write a minimal 65816 asm stub suitable for assembling + linking."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    asm = body if body is not None else dedent("""
        .p816
        .segment "CODE"
        start:
            sei
            clc
            xce
            stp
    """).strip()
    dest.write_text(asm)
    return dest


@libsfx
def test_assemble_produces_object(tmp_path):
    src = _write_stub_asm(tmp_path / "stub.s")
    asm = Ca65Assembler()
    result = asm.assemble(src)
    assert result.obj.exists()
    assert result.obj.stat().st_size > 0
    assert not result.cached
    assert result.duration_ms >= 0


@libsfx
def test_assemble_cache_hit_second_run(tmp_path):
    src = _write_stub_asm(tmp_path / "stub.s")
    cache = BuildCache(tmp_path / ".cache")
    asm = Ca65Assembler(cache=cache)

    first = asm.assemble(src, out_obj=tmp_path / "first.o")
    assert not first.cached

    second = asm.assemble(src, out_obj=tmp_path / "second.o")
    assert second.cached
    assert second.obj.read_bytes() == first.obj.read_bytes()


@libsfx
def test_assemble_cache_invalidates_on_src_change(tmp_path):
    src = _write_stub_asm(tmp_path / "stub.s")
    cache = BuildCache(tmp_path / ".cache")
    asm = Ca65Assembler(cache=cache)

    asm.assemble(src, out_obj=tmp_path / "a.o")
    _write_stub_asm(src, body=dedent("""
        .p816
        .segment "CODE"
        start:
            nop
            nop
            stp
    """).strip())
    r = asm.assemble(src, out_obj=tmp_path / "b.o")
    assert not r.cached


@libsfx
def test_assemble_error_raises_ca65_error(tmp_path):
    src = _write_stub_asm(tmp_path / "bad.s", body=".bogus_directive\n")
    asm = Ca65Assembler()
    with pytest.raises(Ca65Error) as exc:
        asm.assemble(src)
    assert "ca65 failed" in str(exc.value)


@libsfx
def test_link_produces_rom(tmp_path):
    src = _write_stub_asm(tmp_path / "stub.s")
    asm = Ca65Assembler()
    obj = asm.assemble(src).obj

    cfg = tmp_path / "link.cfg"
    cfg.write_text(_MINIMAL_CFG)

    rom_out = tmp_path / "stub.sfc"
    linker = Ld65Linker(config=cfg)
    result = linker.link([obj], rom_out)

    assert result.rom.exists()
    assert result.rom.stat().st_size == 0x8000  # 32KB per cfg
    assert result.symfile is None
    assert result.mapfile is None


@libsfx
def test_link_debug_level_emits_artifacts(tmp_path):
    src = _write_stub_asm(tmp_path / "stub.s")
    asm = Ca65Assembler(debug=True)
    obj = asm.assemble(src).obj

    cfg = tmp_path / "link.cfg"
    cfg.write_text(_MINIMAL_CFG)

    rom_out = tmp_path / "stub.sfc"
    linker = Ld65Linker(config=cfg, debug_level=2)
    result = linker.link([obj], rom_out)

    assert result.symfile is not None and result.symfile.exists()
    assert result.mapfile is not None and result.mapfile.exists()
    assert result.dbgfile is None


@libsfx
def test_link_error_raises(tmp_path):
    cfg = tmp_path / "link.cfg"
    cfg.write_text(_MINIMAL_CFG)
    linker = Ld65Linker(config=cfg)
    with pytest.raises(Ld65Error):
        linker.link([tmp_path / "nope.o"], tmp_path / "out.sfc")


def test_invalid_debug_level_rejected(tmp_path):
    cfg = tmp_path / "link.cfg"
    cfg.write_text(_MINIMAL_CFG)
    with pytest.raises(ValueError):
        Ld65Linker(config=cfg, debug_level=99)

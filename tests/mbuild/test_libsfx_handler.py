"""Phase I (libsfx-native-integration) — `<libsfx>` MBXML handler.

LibSFXProject.build is heavy (ca65 toolchain) so these tests mock it; a
real-toolchain smoke test is gated behind the libsfx wheel being installed.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from retrotool.mbuild.build import build
from retrotool.mbuild.front_ends.mbxml import parse_mbxml_string
from retrotool.mbuild.handlers import HandlerError, handle_libsfx
from retrotool.mbuild.spec import BuildSpec, Section, SectionKind


def _section(src: str = "game_src", **attrs) -> Section:
    return Section(
        kind=SectionKind.LIBSFX,
        files=[Path(src)],
        attrs={"src": src, **attrs},
        source="test",
    )


def test_libsfx_missing_src_raises(tmp_path):
    s = Section(kind=SectionKind.LIBSFX, attrs={}, source="test")
    rom = bytearray()
    with pytest.raises(HandlerError, match="requires src"):
        handle_libsfx(rom, s, tmp_path)


def test_libsfx_src_not_found(tmp_path):
    s = _section("nope")
    rom = bytearray()
    with pytest.raises(HandlerError, match="libsfx project root not found"):
        handle_libsfx(rom, s, tmp_path)


def test_libsfx_handler_invokes_project_build(tmp_path, monkeypatch):
    (tmp_path / "game_src").mkdir()

    built_rom = tmp_path / "fake.sfc"
    built_bytes = b"\xAA" * 0x8000
    captured = {}

    class _FakeConfig:
        def __init__(self):
            self.debug = 0
            self.stack_size = 0x100
            self.name = "fake"

    class _FakeProject:
        def __init__(self, root):
            self.root = root
            self.cfg = _FakeConfig()

        @classmethod
        def discover(cls, root):
            captured["root"] = root
            return cls(root)

        def build(self, out_rom=None, **_kw):
            captured["debug"] = self.cfg.debug
            captured["stack_size"] = self.cfg.stack_size
            out = out_rom or built_rom
            out.write_bytes(built_bytes)

            class _R:
                pass
            r = _R()
            r.rom = out
            return r

    monkeypatch.setattr("retrotool.asm.libsfx.LibSFXProject", _FakeProject)

    s = _section("game_src", debug="2", **{"stack-size": "0x200"})
    rom = bytearray(b"\x00" * 16)
    wr = handle_libsfx(rom, s, tmp_path)

    assert captured["root"].name == "game_src"
    assert captured["debug"] == 2
    assert captured["stack_size"] == 0x200
    assert bytes(rom) == built_bytes
    assert wr.offset == 0 and wr.length == len(built_bytes)


def test_libsfx_mbxml_parses_section():
    spec = parse_mbxml_string(
        '<build><libsfx src="./game_src" debug="2" out="@rom"/></build>'
    )
    assert len(spec.sections) == 1
    s = spec.sections[0]
    assert s.kind is SectionKind.LIBSFX
    assert s.attrs["src"] == "./game_src"
    assert s.attrs["debug"] == "2"
    assert s.attrs["out"] == "@rom"


def test_libsfx_build_end_to_end_with_rep(tmp_path, monkeypatch):
    """libsfx replaces the canvas; a following <rep> patches bytes on top."""
    (tmp_path / "game_src").mkdir()

    class _FakeConfig:
        def __init__(self):
            self.debug = 0
            self.stack_size = 0x100
            self.name = "fake"

    class _FakeProject:
        def __init__(self, root):
            self.root = root
            self.cfg = _FakeConfig()

        @classmethod
        def discover(cls, root):
            return cls(root)

        def build(self, out_rom=None, **_kw):
            out_rom.write_bytes(b"\x00" * 0x8000)

            class _R:
                pass
            r = _R()
            r.rom = out_rom
            return r

    monkeypatch.setattr("retrotool.asm.libsfx.LibSFXProject", _FakeProject)

    (tmp_path / "patch.bin").write_bytes(b"\xDE\xAD\xBE\xEF")
    mbxml = (
        '<build>'
        '  <libsfx src="./game_src" debug="0"/>'
        '  <rep file="patch.bin" offset="10"/>'
        '</build>'
    )
    (tmp_path / "demo.mbxml").write_text(mbxml)

    spec = parse_mbxml_string(mbxml)
    out = tmp_path / "out.sfc"
    result = build(spec, source_root=tmp_path, out_path=out)

    assert result.rom_path == out
    data = out.read_bytes()
    assert data[0x10:0x14] == b"\xDE\xAD\xBE\xEF"
    assert len(data) == 0x8000

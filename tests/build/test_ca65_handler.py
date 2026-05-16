"""<ca65> handler — assemble + link + overlay-at-offset.

Mirror of test_asar_handler / test_bass_handler. ca65/ld65 are mocked since
CI may lack the toolchain; one real-toolchain smoke test is gated on
`retrotool_libsfx` (the bundled wheel) being importable. Without the wheel
the test still runs against system ca65/ld65 if present on PATH — same
fallback the handler itself uses.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from retrotool.build.handlers import HandlerError, handle_ca65
from retrotool.build.spec import Section, SectionKind


def _section(*, file: str = "p.s", files: list[str] | None = None,
             config: str = "Map.cfg", offset: int = 0x100, **attrs) -> Section:
    section_files = [Path(file)] if file else []
    full_attrs = {"file": file, "config": config, **attrs}
    if files is not None:
        full_attrs["files"] = "|".join(files)
    return Section(
        kind=SectionKind.CA65,
        files=section_files,
        offset=offset,
        attrs=full_attrs,
        source="test",
    )


def test_ca65_missing_offset_raises(tmp_path):
    s = Section(
        kind=SectionKind.CA65,
        files=[Path("p.s")],
        attrs={"file": "p.s", "config": "Map.cfg"},
        source="test",
    )
    rom = bytearray(b"\x00" * 0x1000)
    with pytest.raises(HandlerError, match="requires offset"):
        handle_ca65(rom, s, tmp_path)


def test_ca65_missing_config_raises(tmp_path):
    (tmp_path / "p.s").write_text(".byte $42\n")
    s = Section(
        kind=SectionKind.CA65,
        files=[Path("p.s")],
        offset=0x100,
        attrs={"file": "p.s"},  # no config
        source="test",
    )
    rom = bytearray(b"\x00" * 0x1000)
    with pytest.raises(HandlerError, match="requires config"):
        handle_ca65(rom, s, tmp_path)


def test_ca65_missing_config_file_raises(tmp_path):
    (tmp_path / "p.s").write_text(".byte $42\n")
    # config attr points at a file that doesn't exist
    s = _section(config="ghost.cfg")
    rom = bytearray(b"\x00" * 0x1000)
    with pytest.raises(HandlerError, match="linker config not found"):
        handle_ca65(rom, s, tmp_path)


def test_ca65_missing_source_raises(tmp_path):
    (tmp_path / "Map.cfg").write_text("# stub\n")
    s = _section(file="ghost.s")
    rom = bytearray(b"\x00" * 0x1000)
    with pytest.raises(HandlerError, match="ca65 source not found"):
        handle_ca65(rom, s, tmp_path)


def test_ca65_no_source_files_raises(tmp_path):
    (tmp_path / "Map.cfg").write_text("# stub\n")
    # file=, files= both empty
    s = Section(
        kind=SectionKind.CA65,
        files=[],
        offset=0x100,
        attrs={"config": "Map.cfg"},
        source="test",
    )
    rom = bytearray(b"\x00" * 0x1000)
    with pytest.raises(HandlerError, match="requires file="):
        handle_ca65(rom, s, tmp_path)


def test_ca65_bad_define_raises(tmp_path):
    (tmp_path / "Map.cfg").write_text("# stub\n")
    (tmp_path / "p.s").write_text("")
    s = _section(defines="badformat")
    rom = bytearray(b"\x00" * 0x1000)
    with pytest.raises(HandlerError, match="missing '='"):
        handle_ca65(rom, s, tmp_path)


def test_ca65_bad_debug_level_raises(tmp_path):
    (tmp_path / "Map.cfg").write_text("# stub\n")
    (tmp_path / "p.s").write_text("")
    s = _section(debug="9")
    rom = bytearray(b"\x00" * 0x1000)
    with pytest.raises(HandlerError, match="debug="):
        handle_ca65(rom, s, tmp_path)


def test_ca65_bad_length_raises(tmp_path):
    (tmp_path / "Map.cfg").write_text("# stub\n")
    (tmp_path / "p.s").write_text("")
    s = _section(length="not_an_int")
    rom = bytearray(b"\x00" * 0x1000)
    with pytest.raises(HandlerError, match="length="):
        handle_ca65(rom, s, tmp_path)


def test_ca65_handler_invokes_assembler_and_linker(tmp_path, monkeypatch):
    """Mocked toolchain integration — verifies the handler:
      1. Calls Ca65Assembler.assemble() once per source.
      2. Calls Ld65Linker.link() against the produced objects + config.
      3. Memcopies the linker output into the working ROM at `offset`.
    """
    (tmp_path / "Map.cfg").write_text("# stub config\n")
    (tmp_path / "p.s").write_text(".byte $11, $22, $33\n")
    s = _section(
        offset=0x200,
        defines="DEBUG=1|VER=en",
        cpu="65816",
    )

    captured: dict[str, object] = {}

    class _FakeAsm:
        def __init__(self, **kw):
            captured["asm_init"] = kw

        def assemble(self, src, out_obj):
            out_obj.write_bytes(b"OBJ:" + src.read_bytes())

    class _FakeLink:
        def __init__(self, *, config, lib_dirs, cfg_dirs, debug_level):
            captured["link_init"] = {
                "config": config, "lib_dirs": lib_dirs,
                "cfg_dirs": cfg_dirs, "debug_level": debug_level,
            }

        def link(self, objs, out_rom):
            out_rom.write_bytes(b"\xAA\xBB\xCC\xDD")
            captured["objs"] = list(objs)
            return type("R", (), {
                "rom": out_rom, "symfile": None, "mapfile": None,
                "dbgfile": None, "stdout": "", "stderr": "", "duration_ms": 1,
            })()

    monkeypatch.setattr("retrotool.asm.ca65.Ca65Assembler", _FakeAsm)
    monkeypatch.setattr("retrotool.asm.ca65.Ld65Linker", _FakeLink)

    rom = bytearray(b"\x00" * 0x1000)
    wr = handle_ca65(rom, s, tmp_path)

    assert wr.offset == 0x200 and wr.length == 4
    assert rom[0x200:0x204] == b"\xAA\xBB\xCC\xDD"
    asm_init = captured["asm_init"]
    assert asm_init["cpu"] == "65816"
    assert asm_init["defines"] == {"DEBUG": "1", "VER": "en"}
    assert asm_init["debug"] is False
    link_init = captured["link_init"]
    assert link_init["config"] == (tmp_path / "Map.cfg").resolve()
    assert link_init["debug_level"] == 0


def test_ca65_handler_supports_multi_source(tmp_path, monkeypatch):
    """`files=A.s|B.s` — handler builds one .o per source and links them all."""
    (tmp_path / "Map.cfg").write_text("# stub\n")
    (tmp_path / "a.s").write_text("")
    (tmp_path / "b.s").write_text("")
    (tmp_path / "c.s").write_text("")
    s = _section(file="a.s", files=["b.s", "c.s"])

    objs_seen: list[Path] = []

    class _FakeAsm:
        def __init__(self, **kw): pass
        def assemble(self, src, out_obj):
            out_obj.write_bytes(b"")

    class _FakeLink:
        def __init__(self, **kw): pass
        def link(self, objs, out_rom):
            objs_seen.extend(objs)
            out_rom.write_bytes(b"\xFF")
            return type("R", (), {
                "rom": out_rom, "symfile": None, "mapfile": None,
                "dbgfile": None, "stdout": "", "stderr": "", "duration_ms": 1,
            })()

    monkeypatch.setattr("retrotool.asm.ca65.Ca65Assembler", _FakeAsm)
    monkeypatch.setattr("retrotool.asm.ca65.Ld65Linker", _FakeLink)

    rom = bytearray(b"\x00" * 0x1000)
    handle_ca65(rom, s, tmp_path)
    assert len(objs_seen) == 3  # a.s + b.s + c.s


def test_ca65_handler_pads_short_output(tmp_path, monkeypatch):
    """`length=N` + `pad-byte=` short-link gets right-padded to N bytes."""
    (tmp_path / "Map.cfg").write_text("# stub\n")
    (tmp_path / "p.s").write_text("")
    s = _section(offset=0x100, length="0x10", **{"pad-byte": "0xFF"})

    class _FakeAsm:
        def __init__(self, **kw): pass
        def assemble(self, src, out_obj): out_obj.write_bytes(b"")

    class _FakeLink:
        def __init__(self, **kw): pass
        def link(self, objs, out_rom):
            out_rom.write_bytes(b"\x11\x22")  # 2 bytes — short
            return type("R", (), {
                "rom": out_rom, "symfile": None, "mapfile": None,
                "dbgfile": None, "stdout": "", "stderr": "", "duration_ms": 1,
            })()

    monkeypatch.setattr("retrotool.asm.ca65.Ca65Assembler", _FakeAsm)
    monkeypatch.setattr("retrotool.asm.ca65.Ld65Linker", _FakeLink)

    rom = bytearray(b"\x00" * 0x1000)
    handle_ca65(rom, s, tmp_path)
    assert rom[0x100:0x110] == b"\x11\x22" + b"\xFF" * 14


def test_ca65_handler_truncate_default_rejects(tmp_path, monkeypatch):
    (tmp_path / "Map.cfg").write_text("# stub\n")
    (tmp_path / "p.s").write_text("")
    s = _section(length="2")

    class _FakeAsm:
        def __init__(self, **kw): pass
        def assemble(self, src, out_obj): out_obj.write_bytes(b"")

    class _FakeLink:
        def __init__(self, **kw): pass
        def link(self, objs, out_rom):
            out_rom.write_bytes(b"\x00" * 16)  # exceeds length=2
            return type("R", (), {
                "rom": out_rom, "symfile": None, "mapfile": None,
                "dbgfile": None, "stdout": "", "stderr": "", "duration_ms": 1,
            })()

    monkeypatch.setattr("retrotool.asm.ca65.Ca65Assembler", _FakeAsm)
    monkeypatch.setattr("retrotool.asm.ca65.Ld65Linker", _FakeLink)

    rom = bytearray(b"\x00" * 0x1000)
    with pytest.raises(HandlerError, match="exceeds length"):
        handle_ca65(rom, s, tmp_path)


def test_ca65_handler_truncate_when_opt_in(tmp_path, monkeypatch):
    (tmp_path / "Map.cfg").write_text("# stub\n")
    (tmp_path / "p.s").write_text("")
    s = _section(length="2", **{"allow-truncate": "1"})

    class _FakeAsm:
        def __init__(self, **kw): pass
        def assemble(self, src, out_obj): out_obj.write_bytes(b"")

    class _FakeLink:
        def __init__(self, **kw): pass
        def link(self, objs, out_rom):
            out_rom.write_bytes(b"\x11\x22\x33\x44")  # 4 bytes — gets clipped
            return type("R", (), {
                "rom": out_rom, "symfile": None, "mapfile": None,
                "dbgfile": None, "stdout": "", "stderr": "", "duration_ms": 1,
            })()

    monkeypatch.setattr("retrotool.asm.ca65.Ca65Assembler", _FakeAsm)
    monkeypatch.setattr("retrotool.asm.ca65.Ld65Linker", _FakeLink)

    rom = bytearray(b"\x00" * 0x1000)
    handle_ca65(rom, s, tmp_path)
    assert rom[0x100:0x102] == b"\x11\x22"


def test_ca65_handler_surfaces_assembler_failure(tmp_path, monkeypatch):
    (tmp_path / "Map.cfg").write_text("# stub\n")
    (tmp_path / "p.s").write_text("invalid syntax")
    s = _section()

    from retrotool.asm.ca65 import Ca65Error

    class _FakeAsm:
        def __init__(self, **kw): pass
        def assemble(self, src, out_obj):
            raise Ca65Error("ca65: bogus opcode")

    monkeypatch.setattr("retrotool.asm.ca65.Ca65Assembler", _FakeAsm)
    rom = bytearray(b"\x00" * 0x1000)
    with pytest.raises(HandlerError, match="ca65/ld65 failed"):
        handle_ca65(rom, s, tmp_path)


def test_ca65_handler_surfaces_toolchain_missing(tmp_path, monkeypatch):
    (tmp_path / "Map.cfg").write_text("# stub\n")
    (tmp_path / "p.s").write_text("")
    s = _section()

    from retrotool._toolchain import ToolchainError

    class _FakeAsm:
        def __init__(self, **kw):
            raise ToolchainError("ca65 not on PATH")

    monkeypatch.setattr("retrotool.asm.ca65.Ca65Assembler", _FakeAsm)
    rom = bytearray(b"\x00" * 0x1000)
    with pytest.raises(HandlerError, match="toolchain not available"):
        handle_ca65(rom, s, tmp_path)


def test_ca65_handler_rejects_empty_link_output(tmp_path, monkeypatch):
    (tmp_path / "Map.cfg").write_text("# stub\n")
    (tmp_path / "p.s").write_text("")
    s = _section()

    class _FakeAsm:
        def __init__(self, **kw): pass
        def assemble(self, src, out_obj): out_obj.write_bytes(b"")

    class _FakeLink:
        def __init__(self, **kw): pass
        def link(self, objs, out_rom):
            out_rom.write_bytes(b"")  # empty linker output
            return type("R", (), {
                "rom": out_rom, "symfile": None, "mapfile": None,
                "dbgfile": None, "stdout": "", "stderr": "", "duration_ms": 1,
            })()

    monkeypatch.setattr("retrotool.asm.ca65.Ca65Assembler", _FakeAsm)
    monkeypatch.setattr("retrotool.asm.ca65.Ld65Linker", _FakeLink)
    rom = bytearray(b"\x00" * 0x1000)
    with pytest.raises(HandlerError, match="empty linker output"):
        handle_ca65(rom, s, tmp_path)


@pytest.mark.skipif(
    shutil.which("ca65") is None and shutil.which("ld65") is None,
    reason="ca65/ld65 not on PATH (and bundled libsfx wheel not installed)",
)
def test_ca65_real_smoke(tmp_path):
    """Real ca65/ld65 end-to-end if either system or bundled toolchain is
    available. Uses a minimal 1-segment Map.cfg that emits a flat 16-byte
    binary; we then confirm the bytes land at offset $100 in the working
    ROM."""
    (tmp_path / "Map.cfg").write_text(
        "MEMORY {\n"
        "    CODE: start=$0000, size=$10, type=ro, fill=yes, fillval=$00;\n"
        "}\n"
        "SEGMENTS {\n"
        "    CODE: load=CODE, type=ro, optional=no;\n"
        "}\n"
    )
    (tmp_path / "p.s").write_text(
        ".segment \"CODE\"\n"
        ".byte $42, $43, $44, $45\n"
    )
    s = _section(offset=0x100)
    rom = bytearray(b"\x00" * 0x1000)
    handle_ca65(rom, s, tmp_path)
    # ca65/ld65 emits exactly 16 bytes (CODE size); first 4 are our literals.
    assert rom[0x100:0x104] == b"\x42\x43\x44\x45"

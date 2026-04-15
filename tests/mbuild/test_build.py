"""Phase 2 build pipeline tests.

Builds a synthetic LoROM ROM (header + valid checksum) and exercises rep / ins /
bin / script handlers, plus pad + checksum post-process. Smoke-test goal: a
trivial MBXML that does byte replacement actually mutates the output ROM."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from retrotool.mbuild import (
    BuildSpec,
    HandlerError,
    Section,
    SectionKind,
    build,
    parse_mbxml,
)


# ---- fixture: minimal LoROM ROM with valid checksum -----------------------

_ROM_SIZE = 0x80_000  # 512KB — smallest valid SNES size


def _make_lorom(tmp_path: Path, *, fill: int = 0x00) -> Path:
    body = bytearray([fill] * _ROM_SIZE)
    # Internal header @ 0x7FC0.
    title = b"TEST ROM             "  # 21 bytes
    body[0x7FC0:0x7FC0 + 21] = title
    body[0x7FD5] = 0x20         # map_mode = LoROM
    body[0x7FD6] = 0x00         # cartridge_type
    body[0x7FD7] = 0x09         # rom_size = 2^9 = 512KB
    body[0x7FD8] = 0x00         # ram_size
    body[0x7FD9] = 0x01         # country
    body[0x7FDA] = 0x33         # developer
    body[0x7FDB] = 0x00         # version
    # Reset the checksum bytes; recompute over the whole body.
    body[0x7FDC:0x7FE0] = b"\xFF\xFF\x00\x00"
    csum = sum(body) & 0xFFFF
    comp = csum ^ 0xFFFF
    body[0x7FDC] = comp & 0xFF
    body[0x7FDD] = (comp >> 8) & 0xFF
    body[0x7FDE] = csum & 0xFF
    body[0x7FDF] = (csum >> 8) & 0xFF

    p = tmp_path / "base.sfc"
    p.write_bytes(body)
    return p


# ---- handler tests --------------------------------------------------------


def test_rep_writes_at_offset(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "patch.bin").write_bytes(b"\xAA\xBB\xCC\xDD")
    spec = BuildSpec(
        sections=[Section(kind=SectionKind.REP, offset=0x100,
                          files=[PurePosixPath("patch.bin")])],
    )
    out = tmp_path / "out.sfc"
    result = build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    body = out.read_bytes()
    assert body[0x100:0x104] == b"\xAA\xBB\xCC\xDD"
    assert result.sections[0].write.offset == 0x100
    assert result.sections[0].write.length == 4


def test_rep_refuses_growth(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "huge.bin").write_bytes(b"\xFF" * 16)
    spec = BuildSpec(
        sections=[Section(kind=SectionKind.REP, offset=_ROM_SIZE - 4,
                          files=[PurePosixPath("huge.bin")])],
    )
    with pytest.raises(HandlerError, match="would extend ROM"):
        build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path)


def test_ins_grows_rom(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "tail.bin").write_bytes(b"\x11\x22\x33\x44")
    spec = BuildSpec(
        sections=[Section(kind=SectionKind.INS, offset=_ROM_SIZE,
                          files=[PurePosixPath("tail.bin")])],
    )
    out = tmp_path / "out.sfc"
    result = build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    assert result.rom_size == _ROM_SIZE + 4
    body = out.read_bytes()
    assert body[_ROM_SIZE:_ROM_SIZE + 4] == b"\x11\x22\x33\x44"


def test_ins_concatenates_multiple_files(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "a.bin").write_bytes(b"AAAA")
    (tmp_path / "b.bin").write_bytes(b"BBBB")
    spec = BuildSpec(
        sections=[Section(kind=SectionKind.INS, offset=0x200,
                          files=[PurePosixPath("a.bin"), PurePosixPath("b.bin")])],
    )
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    assert out.read_bytes()[0x200:0x208] == b"AAAABBBB"


def test_bin_unified_handler_replace(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "p.bin").write_bytes(b"\xDE\xAD")
    spec = BuildSpec(
        sections=[Section(kind=SectionKind.BIN, offset=0x300,
                          files=[PurePosixPath("p.bin")], grow="replace")],
    )
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    assert out.read_bytes()[0x300:0x302] == b"\xDE\xAD"


def test_bin_size_pads(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "p.bin").write_bytes(b"\xAB")
    spec = BuildSpec(
        sections=[Section(kind=SectionKind.BIN, offset=0x400,
                          files=[PurePosixPath("p.bin")], size=4)],
    )
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    assert out.read_bytes()[0x400:0x404] == b"\xAB\x00\x00\x00"


def test_revbyte_patched(tmp_path):
    rom_path = _make_lorom(tmp_path)
    spec = BuildSpec(revbyteloc=0x7FDB, revision="07")
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    assert out.read_bytes()[0x7FDB] == 0x07


def test_pad_to_next_size(tmp_path):
    """If the ROM grew past 512KB but under 1MB, pad=true bumps to 1MB."""
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "blob.bin").write_bytes(b"\xCC" * 0x1000)
    spec = BuildSpec(
        pad=True,
        sections=[Section(kind=SectionKind.INS, offset=_ROM_SIZE,
                          files=[PurePosixPath("blob.bin")])],
    )
    out = tmp_path / "out.sfc"
    result = build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    assert result.rom_size == 0x100_000  # 1MB


def test_checksum_recomputed_after_changes(tmp_path):
    from retrotool.core.rom import detect_header
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "p.bin").write_bytes(b"\x55" * 32)
    spec = BuildSpec(
        sections=[Section(kind=SectionKind.REP, offset=0x500,
                          files=[PurePosixPath("p.bin")])],
    )
    out = tmp_path / "out.sfc"
    result = build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    h = detect_header(out.read_bytes())
    assert h is not None
    assert (h.checksum ^ h.checksum_complement) == 0xFFFF
    assert h.checksum == result.checksum


# ---- end-to-end MBXML smoke test ------------------------------------------


def test_smoke_build_from_mbxml(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "p.bin").write_bytes(b"\xCA\xFE\xBA\xBE")
    mbxml = tmp_path / "build.mbxml"
    mbxml.write_text(
        f'<build original="{rom_path.name}" name="Demo" pad="true">'
        '<rep file="p.bin" offset="600"/>'
        '</build>'
    )
    spec = parse_mbxml(mbxml)
    out = tmp_path / "demo.sfc"
    result = build(spec, source_root=tmp_path, out_path=out)
    assert result.rom_path == out
    assert out.read_bytes()[0x600:0x604] == b"\xCA\xFE\xBA\xBE"


# ---- script handler -------------------------------------------------------


def test_script_handler_basic(tmp_path):
    rom_path = _make_lorom(tmp_path)
    # Minimal ASCII table.
    tbl = tmp_path / "ascii.tbl"
    tbl.write_text("\n".join(f"{ord(c):02X}={c}" for c in "HELLO WORLD") + "\n",
                   encoding="utf-8")
    txt = tmp_path / "lines.txt"
    txt.write_text("HELLO\nWORLD\n", encoding="utf-8")
    spec = BuildSpec(
        sections=[Section(
            kind=SectionKind.SCRIPT,
            offset=0x700,
            files=[PurePosixPath("lines.txt")],
            table=PurePosixPath("ascii.tbl"),
        )],
    )
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    written = out.read_bytes()[0x700:0x70C]
    assert written.startswith(b"HELLO\x00WORLD\x00")


# ---- only/skip filters ----------------------------------------------------


def test_only_filters_to_kind(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "a.bin").write_bytes(b"\x11\x22")
    (tmp_path / "b.bin").write_bytes(b"\x33\x44")
    spec = BuildSpec(sections=[
        Section(kind=SectionKind.REP, offset=0x500, files=[PurePosixPath("a.bin")]),
        Section(kind=SectionKind.BIN, offset=0x510, files=[PurePosixPath("b.bin")]),
    ])
    out = tmp_path / "out.sfc"
    result = build(spec, source_root=tmp_path, out_path=out,
                   original_rom=rom_path, only={"bin"})
    body = out.read_bytes()
    assert body[0x500:0x502] == b"\x00\x00"  # rep skipped
    assert body[0x510:0x512] == b"\x33\x44"  # bin ran
    assert len(result.skipped) == 1 and result.skipped[0].kind is SectionKind.REP


def test_skip_excludes_kind(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "a.bin").write_bytes(b"\x11\x22")
    (tmp_path / "b.bin").write_bytes(b"\x33\x44")
    spec = BuildSpec(sections=[
        Section(kind=SectionKind.REP, offset=0x500, files=[PurePosixPath("a.bin")]),
        Section(kind=SectionKind.BIN, offset=0x510, files=[PurePosixPath("b.bin")]),
    ])
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out,
          original_rom=rom_path, skip={"bin"})
    body = out.read_bytes()
    assert body[0x500:0x502] == b"\x11\x22"
    assert body[0x510:0x512] == b"\x00\x00"

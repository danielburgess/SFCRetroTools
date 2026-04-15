"""Phase 5.2 — `if=` condition wiring in build pipeline."""
from __future__ import annotations

from pathlib import Path

from retrotool.mbuild import build, parse_mbxml_string
from tests.mbuild.test_build import _make_lorom


def _spec(tmp_path: Path, version: str) -> str:
    (tmp_path / "patch.bin").write_bytes(b"\xAA\xBB")
    return f"""<build name="t" original="base.sfc" version="{version}">
      <ins file="patch.bin" offset="100" if="${{version}}==en"/>
      <ins file="patch.bin" offset="200" if="${{version}}!=en"/>
    </build>"""


def test_condition_skips_false_branch(tmp_path):
    rom_path = _make_lorom(tmp_path)
    spec = parse_mbxml_string(_spec(tmp_path, "en"))
    out = tmp_path / "out.sfc"
    res = build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    body = out.read_bytes()
    assert body[0x100:0x102] == b"\xAA\xBB"
    assert body[0x200:0x202] != b"\xAA\xBB"
    assert len(res.sections) == 1
    assert len(res.skipped) == 1
    assert res.skipped[0].offset == 0x200


def test_condition_via_define_override(tmp_path):
    rom_path = _make_lorom(tmp_path)
    spec = parse_mbxml_string(_spec(tmp_path, "ja"), defines={"version": "en"})
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out, original_rom=rom_path)
    body = out.read_bytes()
    assert body[0x100:0x102] == b"\xAA\xBB"  # define flipped to en → first branch ran

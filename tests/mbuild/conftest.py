"""Shared fixtures for mbuild tests.

Prior iterations had `_make_lorom` duplicated (with minor divergences) across
every test module, plus cross-test imports like
`from tests.mbuild.test_build import _make_lorom`. Factor the builder here
and expose it as a fixture so tests stay independent of each other's layout.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_ROM_SIZE = 0x80_000


def _build_lorom(tmp_path: Path, *, fill: int = 0x00, marker: bytes = b"",
                 plant: dict[int, bytes] | None = None,
                 filename: str | None = None) -> Path:
    """Construct a minimal valid LoROM ROM file in `tmp_path`.

    - `fill`: byte used for unwritten regions.
    - `marker`: convenience — writes bytes at 0x100 (commonly used sentinel).
    - `plant`: arbitrary {offset: bytes} payloads written before checksum calc.
    - `filename`: override output filename. Defaults to `base.sfc` or marker-suffixed.
    """
    body = bytearray([fill] * _ROM_SIZE)
    body[0x7FC0:0x7FC0 + 21] = b"TEST ROM             "
    body[0x7FD5] = 0x20  # LoROM
    body[0x7FD6] = 0x00
    body[0x7FD7] = 0x09  # 512KB
    body[0x7FD8] = 0x00
    body[0x7FD9] = 0x01
    body[0x7FDA] = 0x33
    body[0x7FDB] = 0x00
    if marker:
        body[0x100:0x100 + len(marker)] = marker
    for off, payload in (plant or {}).items():
        body[off:off + len(payload)] = payload
    body[0x7FDC:0x7FE0] = b"\xFF\xFF\x00\x00"
    csum = sum(body) & 0xFFFF
    comp = csum ^ 0xFFFF
    body[0x7FDC] = comp & 0xFF
    body[0x7FDD] = (comp >> 8) & 0xFF
    body[0x7FDE] = csum & 0xFF
    body[0x7FDF] = (csum >> 8) & 0xFF

    if filename is None:
        filename = f"rom_{marker.hex()}.sfc" if marker else "base.sfc"
    p = tmp_path / filename
    p.write_bytes(body)
    return p


@pytest.fixture
def make_lorom(tmp_path):
    """Factory fixture returning a LoROM-builder bound to this test's tmp_path."""
    def _factory(**kwargs) -> Path:
        return _build_lorom(tmp_path, **kwargs)
    return _factory


# Module-level helper callers that import rather than request the fixture.
# Kept for test_build_cache.py / test_conditionals.py which used to cross-import.
def _make_lorom(tmp_path: Path, **kwargs) -> Path:
    return _build_lorom(tmp_path, **kwargs)

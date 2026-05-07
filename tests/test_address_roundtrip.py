"""SFCAddress PC↔SNES round-trip property tests across every mapping.

Originally an interactive `print()`-based harness in `retrotool/snes.py`
(`run_test`/`test_conv`); recast as pytest assertions so failures are
collected by CI rather than lost in stdout.
"""
from __future__ import annotations

import pytest

from retrotool.core.address import SFCAddress

# (pc_to_x, x_to_pc, label, kwargs)
_CONVERSIONS = [
    (SFCAddress.pc_to_lorom1, SFCAddress.lorom1_to_pc, "LOROM1",
     {"fallback": True, "verbose": False}),
    (SFCAddress.pc_to_lorom2, SFCAddress.lorom2_to_pc, "LOROM2", {}),
    (SFCAddress.pc_to_hirom, SFCAddress.hirom_to_pc, "HIROM", {}),
    (SFCAddress.pc_to_exlorom, SFCAddress.exlorom_to_pc, "EXLOROM", {}),
    (SFCAddress.pc_to_exhirom, SFCAddress.exhirom_to_pc, "EXHIROM", {}),
]


@pytest.mark.parametrize("forward,reverse,label,kwargs", _CONVERSIONS,
                         ids=[c[2] for c in _CONVERSIONS])
@pytest.mark.parametrize("pc", list(range(0, 0x7FFFFF, 0x8000)))
def test_pc_roundtrip(forward, reverse, label, kwargs, pc):
    snes = forward(pc)
    if snes is None:
        pytest.skip(f"{label}: PC {pc:#08X} has no SNES address")
    back = reverse(snes, **kwargs)
    assert back == pc, (
        f"{label}: {pc:#08X} → {snes:#08X} → "
        f"{back if back is None else f'{back:#08X}'} (expected {pc:#08X})"
    )

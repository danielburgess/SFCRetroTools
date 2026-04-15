"""SNES ROM header fix + verify via bundled SuperFamicheck.

Thin subprocess wrapper. Before/after checksum pairs are read from the ROM
itself (via `retrotool.core.rom.detect_header`) so the caller gets structured
data without parsing SuperFamicheck's stdout.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from retrotool import _toolchain
from retrotool.core.rom import detect_header


class SuperfamicheckError(RuntimeError):
    """Raised when superfamicheck exits non-zero on a fix attempt."""


@dataclass
class HeaderFixResult:
    path: Path
    checksum_before: int
    complement_before: int
    checksum_after: int
    complement_after: int
    stdout: str
    stderr: str

    @property
    def was_valid(self) -> bool:
        return (self.checksum_before ^ self.complement_before) == 0xFFFF and self.checksum_before != 0

    @property
    def is_valid(self) -> bool:
        return (self.checksum_after ^ self.complement_after) == 0xFFFF and self.checksum_after != 0


def _read_checksum(path: Path) -> tuple[int, int]:
    h = detect_header(path.read_bytes())
    return h.checksum, h.checksum_complement


def fix_rom_header(
    sfc: Path,
    *,
    out: Path | None = None,
    backup: bool = False,
    silent: bool = True,
) -> HeaderFixResult:
    """Fix SNES header + checksum via superfamicheck.

    If `out` is None, fixes in place. If `backup=True`, writes the original
    bytes to `<sfc>.bak` before mutating. Returns before/after checksum pair.
    """
    sfc = Path(sfc)
    if not sfc.exists():
        raise FileNotFoundError(sfc)

    ck_before, comp_before = _read_checksum(sfc)

    if backup and out is None:
        shutil.copy2(sfc, sfc.with_suffix(sfc.suffix + ".bak"))

    target = Path(out) if out else sfc
    if out is not None:
        target.parent.mkdir(parents=True, exist_ok=True)

    cmd = [str(_toolchain.superfamicheck()), str(sfc), "-f"]
    if silent:
        cmd.append("-S")
    if out is not None:
        cmd += ["-o", str(target)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SuperfamicheckError(
            f"superfamicheck failed ({proc.returncode})\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )

    ck_after, comp_after = _read_checksum(target)
    return HeaderFixResult(
        path=target,
        checksum_before=ck_before,
        complement_before=comp_before,
        checksum_after=ck_after,
        complement_after=comp_after,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def verify_rom(sfc: Path) -> bool:
    """Verify a ROM's header checksum/complement. True iff valid.

    Direct header read — superfamicheck always exits 0, so we can't use its
    exit code to signal validity.
    """
    sfc = Path(sfc)
    if not sfc.exists():
        raise FileNotFoundError(sfc)
    ck, comp = _read_checksum(sfc)
    return (ck ^ comp) == 0xFFFF and ck != 0

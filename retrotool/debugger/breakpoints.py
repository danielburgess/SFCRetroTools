"""ca65/ld65 symbol file → Mesen/bsnes-plus breakpoint converter.

Pure-Python port of libSFX's `make_breakpoints` bash script with an
extended convention: optional `;<rwx>[:<mem>]` annotations on `al` lines
select the breakpoint flags per symbol.

Symbol file format (ca65 `--label-file`)::

    al 008000 .main_loop        ;x
    al 7E1000 .player_hp        ;w
    al 008100 .sound_driver     ;x:smp
    al 002100 .inidisp          ;rw:cpu

Output (`.bp`) is one bsnes-plus / Mesen breakpoint per line::

    -b 008000:x:cpu
    -b 7E1000:w:cpu
    -b 008100:x:smp
    -b 002100:rw:cpu
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_VALID_RWX = frozenset("rwx")
_VALID_MEM = frozenset({"cpu", "smp", "vram", "oam", "cgram", "sa1", "sfx"})

# `al 00C000 .label` with optional trailing `;rwx[:mem]` hint.
_SYM_RE = re.compile(
    r"^\s*al\s+([0-9A-Fa-f]+)\s+\.(\S+)"
    r"(?:[^;]*;\s*(\S+?)(?::(\S+))?\s*$)?",
)


class BreakpointError(ValueError):
    """Raised on malformed symbol annotations."""


@dataclass(frozen=True)
class Breakpoint:
    address: int
    label: str
    rwx: str = "x"
    mem: str = "cpu"

    def token(self) -> str:
        return f"-b {self.address:06X}:{self.rwx}:{self.mem}"


def parse_symfile(symfile: Path) -> list[Breakpoint]:
    """Parse a ca65 .sym/.dsym file. Only lines with a `;rwx[:mem]` hint become breakpoints."""
    symfile = Path(symfile)
    if not symfile.exists():
        raise FileNotFoundError(symfile)

    out: list[Breakpoint] = []
    for lineno, line in enumerate(symfile.read_text().splitlines(), 1):
        m = _SYM_RE.match(line)
        if not m:
            continue
        addr_hex, label, rwx, mem = m.groups()
        if rwx is None:
            continue  # unannotated symbol — not a breakpoint
        if set(rwx) - _VALID_RWX:
            raise BreakpointError(f"{symfile}:{lineno}: invalid rwx flags {rwx!r}")
        mem = mem or "cpu"
        if mem not in _VALID_MEM:
            raise BreakpointError(f"{symfile}:{lineno}: unknown memory space {mem!r}")
        out.append(Breakpoint(address=int(addr_hex, 16), label=label, rwx=rwx, mem=mem))
    return out


def make_mesen_breakpoints(symfile: Path, out_bp: Path | None = None) -> Path:
    """Convert a ca65 .sym file → breakpoint file.

    If `out_bp` is omitted, writes `<symfile>.bp` next to the input.
    """
    symfile = Path(symfile)
    bps = parse_symfile(symfile)
    target = Path(out_bp) if out_bp else symfile.with_suffix(symfile.suffix + ".bp")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(bp.token() for bp in bps) + ("\n" if bps else ""))
    return target


def read_breakpoints(bp_file: Path) -> list[Breakpoint]:
    """Parse a `.bp` file previously written by `make_mesen_breakpoints`."""
    bp_file = Path(bp_file)
    if not bp_file.exists():
        raise FileNotFoundError(bp_file)
    out: list[Breakpoint] = []
    for lineno, line in enumerate(bp_file.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith("-b "):
            raise BreakpointError(f"{bp_file}:{lineno}: expected `-b` token, got {line!r}")
        spec = line[3:]
        parts = spec.split(":")
        if len(parts) < 2 or len(parts) > 3:
            raise BreakpointError(f"{bp_file}:{lineno}: malformed spec {spec!r}")
        addr_hex, rwx = parts[0], parts[1]
        mem = parts[2] if len(parts) == 3 else "cpu"
        out.append(Breakpoint(address=int(addr_hex, 16), label="", rwx=rwx, mem=mem))
    return out


# Mesen2 memoryType names keyed by libSFX memory-space label.
_MESEN_MEMORY_TYPE = {
    "cpu": "SnesMemory",
    "smp": "Spc700Memory",
    "vram": "SnesVideoRam",
    "oam": "SnesSpriteRam",
    "cgram": "SnesCgRam",
    "sa1": "Sa1Memory",
    "sfx": "GsuMemory",
}

_MESEN_BREAK_TYPE = {"r": "read", "w": "write", "x": "exec"}


def to_mesen_calls(bps: Iterable[Breakpoint]) -> list[tuple[int, str, str]]:
    """Expand breakpoints into `(address, memoryType, break_on)` triples for `MesenClient.add_breakpoint`.

    A single bp with rwx="rw" expands to two calls (read + write).
    """
    out: list[tuple[int, str, str]] = []
    for bp in bps:
        mem_type = _MESEN_MEMORY_TYPE.get(bp.mem)
        if mem_type is None:
            raise BreakpointError(f"no Mesen memoryType mapping for {bp.mem!r}")
        for flag in bp.rwx:
            out.append((bp.address, mem_type, _MESEN_BREAK_TYPE[flag]))
    return out

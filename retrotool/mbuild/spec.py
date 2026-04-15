"""Canonical `BuildSpec` — the in-memory form both front-ends produce.

Kept front-end agnostic: no XML / TOML types leak in here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Optional

OP_REPLACE = "replace"
OP_INSERT = "insert"


class SectionKind(str, Enum):
    # raw bytes
    REP = "rep"
    INS = "ins"
    # LZ compressed
    LZR = "lzr"
    LZI = "lzi"
    # RLE compressed
    RLR = "rlr"
    RLI = "rli"
    # bitplane converted
    BPR = "bpr"
    BPI = "bpi"
    # script-built (text + table → binary)
    SBR = "sbr"
    SBI = "sbi"
    # retrotool extensions
    BIN = "bin"
    ASAR = "asar"
    GRAPHICS = "graphics"
    SCRIPT = "script"
    PROJECT = "project"
    ASARDEF = "asardef"
    LIBSFX = "libsfx"
    FIXED_RECORDS = "fixed-records"

    @property
    def operation(self) -> str:
        last = self.value[-1]
        if self.value in ("bin", "asar", "graphics", "script", "project",
                          "asardef", "libsfx", "fixed-records"):
            return OP_REPLACE  # default; actual behavior handler-specific
        return OP_INSERT if last == "i" else OP_REPLACE


# MBuild 1.29 section tags we accept as-is.
MBUILD_KINDS = frozenset({
    SectionKind.REP, SectionKind.INS,
    SectionKind.LZR, SectionKind.LZI,
    SectionKind.RLR, SectionKind.RLI,
    SectionKind.BPR, SectionKind.BPI,
    SectionKind.SBR, SectionKind.SBI,
})


@dataclass
class Section:
    """One element in a build. Shared / extract-only / build-only attrs all live here;
    handlers pick the ones they care about for the operation being performed."""
    kind: SectionKind
    offset: Optional[int] = None            # ROM offset (absolute, post-header-strip for SMC)
    files: list[PurePosixPath] = field(default_factory=list)
    codec: Optional[str] = None             # lztype / rletype / bptype / graphics encode
    table: Optional[PurePosixPath] = None   # script table file (sbr/sbi)
    # retrotool extensions (unified attrs — handlers ignore what doesn't apply)
    size: Optional[int] = None
    bpp: Optional[int] = None
    count: Optional[int] = None
    pointer_table: Optional[int] = None
    pad_to: Optional[int] = None
    grow: Optional[str] = None              # "insert" | "replace" | "fail"
    dedupe: bool = False
    stride: Optional[int] = None            # bytes per record (fixed-records)
    condition: Optional[str] = None         # if="${version}==english"
    # raw parsed attrs kept for forward-compat / unknown-attr diagnostics
    attrs: dict[str, str] = field(default_factory=dict)
    # front-end provenance (file:line if known)
    source: Optional[str] = None
    # Set when the parser auto-migrated a MBuild 1.29 legacy element
    # (e.g. <lzr> → kind=BIN, original_kind=LZR). None for native-form sections.
    original_kind: Optional["SectionKind"] = None
    # Transient: bytes populated by `parallel_prepare` for eligible kinds; the
    # serial handler path checks this before re-encoding. Not compared or repr'd.
    _prepared: Optional[bytes] = field(default=None, repr=False, compare=False)


@dataclass
class BuildSpec:
    """Parsed build description. Produced by any front-end, consumed by build/extract."""
    original: Optional[PurePosixPath] = None
    name: Optional[str] = None
    version: Optional[str] = None
    revision: Optional[str] = None
    revbyteloc: Optional[int] = None        # ROM offset where revision byte lives
    path: Optional[PurePosixPath] = None    # build-files root (relative to mbxml file)
    pad: bool = False
    diff: Optional[str] = None              # "xdelta" | "ips" | None
    sections: list[Section] = field(default_factory=list)
    source_path: Optional[PurePosixPath] = None  # where this spec was parsed from
    # Variables resolved at parse time (built-ins + user defines). Carried so
    # build-time evaluators (e.g. `if=` conditions) see the same scope as the
    # front-end did.
    vars: dict[str, str] = field(default_factory=dict)

    def iter_kind(self, kind: SectionKind):
        return (s for s in self.sections if s.kind == kind)

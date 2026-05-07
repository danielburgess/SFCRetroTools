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
    LIBSFX = "libsfx"
    FIXED_RECORDS = "fixed-records"
    WINDOWED_SCRIPT = "windowed-script"

    @property
    def operation(self) -> str:
        last = self.value[-1]
        if self.value in ("bin", "asar", "graphics", "script", "project",
                          "libsfx", "fixed-records", "windowed-script"):
            return OP_REPLACE  # default; actual behavior handler-specific
        return OP_INSERT if last == "i" else OP_REPLACE


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
    # Per-field record schema for fixed-records: list of
    # {label, start, len, fill}. Populated from a DataDef's `[[fields]]`
    # (or inline attrs). When set, handle_fixed_records treats `file=` as
    # a text source to pack; when None, `file=` is read as a pre-packed
    # `stride*count` binary (backwards-compat path).
    fields: Optional[list[dict]] = None
    condition: Optional[str] = None         # if="${version}==english"
    # Per-section cache override:
    #   None  — follow `_CACHEABLE_KINDS` default
    #   True  — force cache on (opt-in for kinds that are uncached by default,
    #           e.g. <asar>, where the user has verified the patch writes are
    #           independent of prior ROM state)
    #   False — force cache off (opt-out for an otherwise cacheable kind)
    # ASAR with cache=True uses diff-mode writes (only changed-byte runs are
    # stored/replayed) and transitively hashes incsrc/incbin dependencies.
    cache: Optional[bool] = None
    # script-handler extras (LM3 parity)
    pointer_size: Optional[int] = None      # 2 or 3 — pointer width in table
    terminator: Optional[int] = None        # entry terminator byte (default 0x00)
    fallback_table: Optional[PurePosixPath] = None
    word_wrap: Optional[dict] = None        # {line_width, max_lines, entries}
    # Per-entry override for windowed-script "preserve byte at $START". Empty
    # = use legacy default (preserve byte at $START, FFC0 stub at $START+1).
    # Listed entry indices flip to clobber-mode (FFC0 stub at $START, byte
    # there overwritten). See BuildStep.clobber_lead_entries.
    clobber_lead_entries: list = field(default_factory=list)
    textbuf_limit: Optional[int] = None
    overflow: Optional[dict] = None         # {strategy, marker, splitter, ...}
    # Placement mode: "overflow" = in-place patch + window redirects (ptr
    # table untouched); "relocate" = rewrite ptr table into new data region.
    # When None, handler auto-detects (windowed syntax in source file →
    # overflow, otherwise → relocate).
    placement: Optional[dict] = None
    # When a Section was synthesized from a DataDef's `[section]` sub-table,
    # the DataDef name is recorded here. Populated by `sections_from_datadefs`;
    # never set by TOML/MBXML parsers directly. Informational only.
    from_datadef: Optional[str] = None
    # raw parsed attrs kept for forward-compat / unknown-attr diagnostics
    attrs: dict[str, str] = field(default_factory=dict)
    # front-end provenance (file:line if known)
    source: Optional[str] = None
    # Set when the parser auto-migrated a MBuild 1.29 legacy element
    # (e.g. <lzr> → kind=BIN, original_kind=LZR). None for native-form sections.
    original_kind: Optional["SectionKind"] = None


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
    pad_byte: int = 0x00                    # Byte value used for ROM expansion + tail pad.
    diff: Optional[str] = None              # "xdelta" | "ips" | None
    sections: list[Section] = field(default_factory=list)
    source_path: Optional[PurePosixPath] = None  # where this spec was parsed from
    # Variables resolved at parse time (built-ins + user defines). Carried so
    # build-time evaluators (e.g. `if=` conditions) see the same scope as the
    # front-end did.
    vars: dict[str, str] = field(default_factory=dict)
    # Top-level freespace ranges (PC half-open) shared across handlers that
    # need overflow allocation. Each pair is [lo, hi).
    freespace: list[tuple[int, int]] = field(default_factory=list)
    # Global label registry populated from `[[build.labels]]`. Sections may
    # also register labels dynamically via `export-label=`.
    labels: dict[str, int] = field(default_factory=dict)
    # Explicit pipeline order (by section key — DataDef name or inline name=).
    # When set, listed names come first in the given order; remaining sections
    # follow, sorted by offset. `None` means auto-sort-by-offset.
    order: Optional[list[str]] = None
    # Project-level defaults for DataDef-derived sections. Parsed from
    # `[rom.build.section.overflow]` / `[rom.build.section.placement]` in
    # project.toml. Each datadef inherits these unless its own `[section]`
    # sub-table redeclares the same top-level key (full-key override, no
    # deep merge). Inline `[[rom.build.sections]]` are NOT affected.
    section_defaults: dict = field(default_factory=dict)
    # Project-level default source-data directory for script files. Parsed
    # from the top-level `en_data_dir=` scalar in project.toml. Resolver
    # uses this to synthesize `{en_data_dir}/{datadef.name}.txt` when a
    # DataDef's `[section]` omits both `en_file=` and `file=`.
    en_data_dir: Optional[str] = None
    # Generic `{lang}_data_dir=` scalars from project.toml top level (e.g.
    # en_data_dir, jp_data_dir, de_data_dir). Used by `extract --lang X` to
    # pick a destination root at runtime, independent of which lang the
    # build pipeline treats as primary. Keys are lowercase lang codes.
    data_dirs_by_lang: dict[str, str] = field(default_factory=dict)
    # `[extract]` sub-table from project.toml. Currently only `default_lang`
    # is consumed (picks data_dirs_by_lang[default_lang] when extract is
    # invoked without --lang/--dest). Room for future extract-only config.
    extract_config: dict = field(default_factory=dict)
    # Mesen2 SRAM-sync post-build hook. When `sync_sram=True`, the build
    # driver copies `<mesen_saves_dir>/<source_rom_stem>.srm` to
    # `<mesen_saves_dir>/<out_rom_stem>.srm` after writing the output ROM,
    # so an in-progress save state on the source ROM transfers to the
    # patched ROM. No-op when the source SRM doesn't exist.
    sync_sram: bool = False
    # Override for the Mesen2 Saves directory. `None` → platform default
    # (`~/.config/Mesen2/Saves` on Linux). Expansion of `~` is performed by
    # `retrotool.debugger.mesen_saves.resolve_saves_dir`.
    mesen_saves_dir: Optional[str] = None
    # When `sync_sram=True` and a pre-existing destination SRM would be
    # clobbered, tar.gz the existing file into
    # `<saves>/<dst_stem>_archive_<timestamp>.tar.gz` first. Only active
    # when the destination differs from the source (identical content is
    # redundant to archive). Default True — safe-by-default for iterative
    # playtest runs that might have accumulated state.
    archive_sram: bool = True

    def iter_kind(self, kind: SectionKind):
        return (s for s in self.sections if s.kind == kind)

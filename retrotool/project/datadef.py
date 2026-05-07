"""Per-table data definition parser. Models scripts/*.toml style files."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from retrotool.project.schema import parse_size, parse_snes_addr

TABLE_TYPES = {"pointer", "fixed", "dte", "bytecode"}
COMPRESSION_TYPES = {"none", "lzss", "rle", "custom"}


@dataclass
class EncodingSection:
    table_file: Path
    fallback: Optional[Path] = None
    terminator: int = 0x00


@dataclass
class PointersSection:
    offset: int                       # where the pointer table begins (PC/SNES)
    count: int
    size: int = 2                     # 2 or 3
    bank_override: Optional[int] = None


@dataclass
class DataSection:
    offset: int                       # where the data block begins (PC/SNES)
    end: Optional[int] = None
    compression: str = "none"
    compression_params: dict = field(default_factory=dict)


@dataclass
class BuildStep:
    """`[section]` sub-table in a DataDef. Declares the build-pipeline step
    for this table: what kind of section, which source file to patch with,
    and any build-only strategy (overflow, grow, codec).

    The ROM-structure facts (offset, count, pointer size, encoding table)
    come from the DataDef's other sub-tables — never redeclared here.
    """
    kind: str                         # SectionKind value (e.g. "script", "fixed-records")
    file: Optional[str] = None        # source input file (text / bin). Usually omitted —
                                      # resolver defaults to {project.en_data_dir}/{name}.txt.
                                      # Legacy alias for `en_file`.
    grow: Optional[str] = None        # "insert" | "replace" | "fail"
    codec: Optional[str] = None
    condition: Optional[str] = None   # `if=` expression
    offset: Optional[int] = None      # explicit anchor when DataDef has no pointers/data section
    overflow: dict = field(default_factory=dict)  # strategy/marker/splitter/...
    placement: dict = field(default_factory=dict) # {mode: "overflow"|"relocate"}
    cache: Optional[bool] = None                  # opt-in/out of per-section caching
    # Per-entry override for the windowed-script "preserve byte at $START"
    # behavior. Default = preserve (FFC0 stub at $START+1, byte at $START
    # left in ROM). Listed entry indices flip to clobber-mode (FFC0 stub at
    # $START) for ALL their windows. Useful when the byte at $START is an
    # unwanted JP residual (e.g. full-width space) that would render as an
    # extra leading glyph in front of the window's overflow content.
    clobber_lead_entries: list = field(default_factory=list)
    extras: dict = field(default_factory=dict)    # forward-compat raw attrs


@dataclass
class RelocationSection:
    target: int
    pointer_size: int = 3


@dataclass
class DataDef:
    """A single data table/block definition."""
    name: str
    type: str
    source_path: Optional[Path] = None
    encoding: Optional[EncodingSection] = None
    pointers: Optional[PointersSection] = None
    data: Optional[DataSection] = None
    relocation: Optional[RelocationSection] = None
    section: Optional[BuildStep] = None   # `[section]` sub-table (opt-in build)
    extras: dict = field(default_factory=dict)

    @property
    def anchor_offset(self) -> Optional[int]:
        """Canonical offset for ordering pipeline sections.

        Falls back through pointers → data → [section] so a DataDef with any
        shape has a single answer. Returns None only if none of them set one.
        """
        if self.pointers is not None:
            return self.pointers.offset
        if self.data is not None:
            return self.data.offset
        if self.section is not None and self.section.offset is not None:
            return self.section.offset
        return None


def datadef_from_dict(doc: dict, source_path: Optional[Path] = None) -> DataDef:
    table = doc.get("table") or {}
    name = table.get("name")
    if not name:
        raise ValueError(f"datadef missing [table].name ({source_path})")
    dtype = table.get("type", "pointer")
    if dtype not in TABLE_TYPES:
        raise ValueError(f"datadef {name}: unknown type {dtype!r}. Known: {sorted(TABLE_TYPES)}")

    encoding = None
    if enc := doc.get("encoding"):
        encoding = EncodingSection(
            table_file=Path(enc["table_file"]),
            fallback=Path(enc["fallback"]) if enc.get("fallback") else None,
            terminator=int(enc.get("terminator", 0x00)),
        )

    pointers = None
    if ptr := doc.get("pointers"):
        if "offset" not in ptr:
            raise ValueError(
                f"datadef {name}: [pointers] missing 'offset' "
                f"(was 'address' in legacy schema — rename it)"
            )
        pointers = PointersSection(
            offset=parse_snes_addr(ptr["offset"]),
            count=int(ptr["count"]),
            size=int(ptr.get("size", 2)),
            bank_override=parse_snes_addr(ptr["bank_override"]) if ptr.get("bank_override") else None,
        )
        if pointers.size not in (2, 3):
            raise ValueError(f"datadef {name}: pointer size must be 2 or 3, got {pointers.size}")

    data = None
    if d := doc.get("data"):
        comp = d.get("compression", "none")
        if comp not in COMPRESSION_TYPES:
            raise ValueError(f"datadef {name}: unknown compression {comp!r}")
        if "offset" not in d:
            raise ValueError(
                f"datadef {name}: [data] missing 'offset' "
                f"(was 'start' in legacy schema — rename it)"
            )
        data = DataSection(
            offset=parse_snes_addr(d["offset"]),
            end=parse_snes_addr(d["end"]) if d.get("end") else None,
            compression=comp,
            compression_params=dict(d.get("compression_params") or {}),
        )

    relocation = None
    if r := doc.get("relocation"):
        relocation = RelocationSection(
            target=parse_snes_addr(r["target"]),
            pointer_size=int(r.get("pointer_size", 3)),
        )

    section = None
    sec_doc = doc.get("section")
    # Presence (even empty) signals "include in build pipeline". Empty is
    # valid — all defaults (kind="script", file=en_data_dir/name.txt, etc.)
    # come from project-level config.
    if sec_doc is not None:
        if not isinstance(sec_doc, dict):
            raise ValueError(f"datadef {name}: [section] must be a table")
        # `kind` defaults to "script" — the overwhelmingly common case.
        # Fixed-width tables (unit-names etc) must declare kind="fixed-records"
        # explicitly; other kinds (font, asar, ...) similarly.
        kind = sec_doc.get("kind", "script")
        if not isinstance(kind, str) or not kind:
            raise ValueError(f"datadef {name}: [section].kind must be a string")
        # [section] must not redeclare ROM-structure facts owned by pointers/
        # data/encoding/word_wrap/etc — single source of truth.
        forbidden = {"pointer-table", "pointer-size", "count", "table",
                     "fallback-table", "terminator", "word-wrap",
                     "textbuf-limit", "stride"}
        clashes = [k for k in forbidden if k in sec_doc]
        if clashes:
            raise ValueError(
                f"datadef {name}: [section] cannot redeclare {clashes!r} — "
                f"those live in [pointers]/[data]/[encoding]/[word_wrap]/extras"
            )
        known = {"kind", "file", "en_file", "grow", "codec", "if", "offset",
                 "overflow", "placement", "cache", "clobber_lead_entries"}
        # `en_file` is the preferred key; `file` is kept as a legacy alias.
        # Resolver auto-defaults when both are absent (→ {en_data_dir}/{name}.txt).
        en_file = sec_doc.get("en_file")
        file_alias = sec_doc.get("file")
        if en_file and file_alias:
            raise ValueError(
                f"datadef {name}: [section] has both en_file= and file= — "
                f"pick one (en_file is preferred)"
            )
        placement = sec_doc.get("placement")
        if placement is not None and not isinstance(placement, dict):
            raise ValueError(f"datadef {name}: [section.placement] must be a table")
        cache_raw = sec_doc.get("cache")
        if cache_raw is None:
            cache_val: Optional[bool] = None
        elif isinstance(cache_raw, bool):
            cache_val = cache_raw
        elif isinstance(cache_raw, int):
            cache_val = bool(cache_raw)
        elif isinstance(cache_raw, str):
            s = cache_raw.strip().lower()
            if s in {"true", "1", "yes", "on"}:
                cache_val = True
            elif s in {"false", "0", "no", "off"}:
                cache_val = False
            else:
                raise ValueError(f"datadef {name}: [section].cache={cache_raw!r} not bool-like")
        else:
            raise ValueError(f"datadef {name}: [section].cache must be bool-like")
        section = BuildStep(
            kind=kind,
            file=en_file or file_alias,
            grow=sec_doc.get("grow"),
            codec=sec_doc.get("codec"),
            condition=sec_doc.get("if"),
            offset=parse_snes_addr(sec_doc["offset"]) if sec_doc.get("offset") is not None else None,
            overflow=dict(sec_doc.get("overflow") or {}),
            placement=dict(placement or {}),
            cache=cache_val,
            clobber_lead_entries=list(sec_doc.get("clobber_lead_entries") or []),
            extras={k: v for k, v in sec_doc.items() if k not in known},
        )

    extras = {k: v for k, v in doc.items() if k not in
              {"table", "encoding", "pointers", "data", "relocation",
               "section"}}

    return DataDef(
        name=name,
        type=dtype,
        source_path=source_path,
        encoding=encoding,
        pointers=pointers,
        data=data,
        relocation=relocation,
        section=section,
        extras=extras,
    )

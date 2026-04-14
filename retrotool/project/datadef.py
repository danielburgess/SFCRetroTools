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
    address: int
    count: int
    size: int = 2                     # 2 or 3
    bank_override: Optional[int] = None


@dataclass
class DataSection:
    start: int
    end: Optional[int] = None
    compression: str = "none"
    compression_params: dict = field(default_factory=dict)


@dataclass
class RelocationSection:
    target: int
    pointer_size: int = 3


@dataclass
class DisplaySection:
    word_wrap_width: Optional[int] = None
    word_wrap_lines: Optional[int] = None
    windowed: bool = False


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
    display: Optional[DisplaySection] = None
    extras: dict = field(default_factory=dict)


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
        pointers = PointersSection(
            address=parse_snes_addr(ptr["address"]),
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
        data = DataSection(
            start=parse_snes_addr(d["start"]),
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

    display = None
    if disp := doc.get("display"):
        ww = disp.get("word_wrap") or {}
        display = DisplaySection(
            word_wrap_width=int(ww["width"]) if "width" in ww else None,
            word_wrap_lines=int(ww["lines"]) if "lines" in ww else None,
            windowed=bool(disp.get("windowed", False)),
        )

    extras = {k: v for k, v in doc.items() if k not in
              {"table", "encoding", "pointers", "data", "relocation", "display"}}

    return DataDef(
        name=name,
        type=dtype,
        source_path=source_path,
        encoding=encoding,
        pointers=pointers,
        data=data,
        relocation=relocation,
        display=display,
        extras=extras,
    )

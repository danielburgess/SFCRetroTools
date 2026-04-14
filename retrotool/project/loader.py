"""Load + validate project.toml and data definitions."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Union

from retrotool.project.datadef import DataDef, datadef_from_dict
from retrotool.project.schema import (
    BuildSection,
    DebuggerSection,
    ProjectConfig,
    RomHardware,
    RomSection,
    RomSram,
    RomVectors,
    mapping_to_address_type,
    parse_size,
    parse_snes_addr,
)


def load_project(path: Union[str, Path]) -> ProjectConfig:
    path = Path(path).resolve()
    if path.is_dir():
        path = path / "project.toml"
    if not path.exists():
        raise FileNotFoundError(f"project.toml not found: {path}")
    doc = tomllib.loads(path.read_text())
    return _project_from_dict(doc, root=path.parent)


def _project_from_dict(doc: dict, root: Path) -> ProjectConfig:
    rom_doc = doc.get("rom")
    if not rom_doc:
        raise ValueError("project.toml: missing [rom] section")
    rom = _rom_from_dict(rom_doc)

    build_doc = doc.get("build") or {}
    build = BuildSection(
        assembler=build_doc.get("assembler", "asar"),
        output_dir=Path(build_doc.get("output_dir", "out")),
        cache_dir=Path(build_doc.get("cache_dir", ".cache")),
    )

    dbg_doc = doc.get("debugger") or {}
    debugger = DebuggerSection(
        type=dbg_doc.get("type", "mesen-diz"),
        pipe_name=dbg_doc.get("pipe_name", "auto"),
    )

    data_dirs = [Path(p) for p in (doc.get("data_dirs") or [])]

    reserved = {"rom", "build", "debugger", "data_dirs"}
    extras = {k: v for k, v in doc.items() if k not in reserved}

    _validate(rom)

    return ProjectConfig(
        root=root,
        rom=rom,
        build=build,
        debugger=debugger,
        data_dirs=data_dirs,
        extras=extras,
    )


def _rom_from_dict(d: dict) -> RomSection:
    required = ("name", "file", "mapping", "size")
    missing = [k for k in required if k not in d]
    if missing:
        raise ValueError(f"[rom] missing required keys: {missing}")

    vectors_doc = d.get("vectors") or {}
    vectors = RomVectors(
        reset=_opt_addr(vectors_doc.get("reset")),
        nmi=_opt_addr(vectors_doc.get("nmi")),
        irq=_opt_addr(vectors_doc.get("irq")),
        cop=_opt_addr(vectors_doc.get("cop")),
        brk=_opt_addr(vectors_doc.get("brk")),
        abort=_opt_addr(vectors_doc.get("abort")),
    )

    sram_doc = d.get("sram") or {}
    sram = RomSram(
        start=_opt_addr(sram_doc.get("start")),
        size=parse_size(sram_doc.get("size", 0)) if sram_doc.get("size") else 0,
    )

    hw_doc = d.get("hardware") or {}
    hardware = RomHardware(coprocessor=hw_doc.get("coprocessor", "none"))

    return RomSection(
        name=d["name"],
        file=Path(d["file"]),
        mapping=d["mapping"],
        size=parse_size(d["size"]),
        expanded_size=parse_size(d["expanded_size"]) if d.get("expanded_size") else None,
        header=bool(d.get("header", False)),
        vectors=vectors,
        sram=sram,
        hardware=hardware,
    )


def _opt_addr(v):
    return parse_snes_addr(v) if v is not None else None


def _validate(rom: RomSection) -> None:
    # raises ValueError if mapping unknown
    mapping_to_address_type(rom.mapping)
    if rom.expanded_size is not None and rom.expanded_size < rom.size:
        raise ValueError(f"[rom] expanded_size ({rom.expanded_size}) < size ({rom.size})")


def load_datadef(path: Union[str, Path]) -> DataDef:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"datadef not found: {path}")
    doc = tomllib.loads(path.read_text())
    return datadef_from_dict(doc, source_path=path)


def load_datadefs(project: ProjectConfig) -> list[DataDef]:
    """Load all *.toml from project.data_dirs."""
    defs: list[DataDef] = []
    for rel in project.data_dirs:
        base = rel if rel.is_absolute() else (project.root / rel)
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.toml")):
            defs.append(load_datadef(p))
    return defs

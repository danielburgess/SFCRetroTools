"""Project file schema. Dataclasses model project.toml structure."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from retrotool.core.address import SFCAddressType
from retrotool.core.binary import integer_or_hex

_MAPPING_TO_ADDR_TYPE = {
    "lorom": SFCAddressType.LOROM1,
    "lorom1": SFCAddressType.LOROM1,
    "lorom2": SFCAddressType.LOROM2,
    "hirom": SFCAddressType.HIROM,
    "exlorom": SFCAddressType.EXLOROM,
    "exhirom": SFCAddressType.EXHIROM,
    "sa1": SFCAddressType.LOROM1,
}

_SIZE_SUFFIXES = {"k": 1 << 10, "m": 1 << 20, "g": 1 << 30}


def parse_size(value) -> int:
    """Parse '2M', '8K', 0x10000, 65536 → bytes."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip().lower().replace("_", "").replace(",", "")
        if s.startswith("0x"):
            return int(s, 16)
        if s and s[-1] in _SIZE_SUFFIXES:
            return int(float(s[:-1]) * _SIZE_SUFFIXES[s[-1]])
        return int(s)
    raise ValueError(f"Cannot parse size: {value!r}")


def parse_snes_addr(value) -> int:
    """Parse '$1B:8000', '$C18000', '0xC18000', 0xC18000 → int."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip().replace("$", "").replace(":", "").replace("_", "")
        if s.lower().startswith("0x"):
            s = s[2:]
        return int(s, 16)
    raise ValueError(f"Cannot parse address: {value!r}")


def mapping_to_address_type(mapping: str) -> int:
    key = mapping.strip().lower()
    if key not in _MAPPING_TO_ADDR_TYPE:
        raise ValueError(f"Unknown mapping: {mapping!r}. Known: {sorted(_MAPPING_TO_ADDR_TYPE)}")
    return _MAPPING_TO_ADDR_TYPE[key]


@dataclass
class RomVectors:
    reset: Optional[int] = None
    nmi: Optional[int] = None
    irq: Optional[int] = None
    cop: Optional[int] = None
    brk: Optional[int] = None
    abort: Optional[int] = None


@dataclass
class RomSram:
    start: Optional[int] = None
    size: int = 0


@dataclass
class RomHardware:
    coprocessor: str = "none"


@dataclass
class RomSection:
    name: str
    file: Path
    mapping: str
    size: int
    expanded_size: Optional[int] = None
    header: bool = False
    vectors: RomVectors = field(default_factory=RomVectors)
    sram: RomSram = field(default_factory=RomSram)
    hardware: RomHardware = field(default_factory=RomHardware)

    @property
    def address_type(self) -> int:
        return mapping_to_address_type(self.mapping)


@dataclass
class BuildSection:
    assembler: str = "asar"
    output_dir: Path = field(default_factory=lambda: Path("out"))
    cache_dir: Path = field(default_factory=lambda: Path(".cache"))


@dataclass
class DebuggerSection:
    type: str = "mesen-diz"
    pipe_name: str = "auto"


@dataclass
class ProjectConfig:
    """Root project.toml model."""
    root: Path
    rom: RomSection
    build: BuildSection = field(default_factory=BuildSection)
    debugger: DebuggerSection = field(default_factory=DebuggerSection)
    data_dirs: list[Path] = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    @property
    def rom_path(self) -> Path:
        return self.rom.file if self.rom.file.is_absolute() else (self.root / self.rom.file)

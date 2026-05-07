"""retrotool.project — project file loading + data definitions."""
from retrotool.project.datadef import (
    COMPRESSION_TYPES,
    DataDef,
    DataSection,
    EncodingSection,
    PointersSection,
    RelocationSection,
    TABLE_TYPES,
    datadef_from_dict,
)
from retrotool.project.loader import load_datadef, load_datadefs, load_project
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

__all__ = [
    "ProjectConfig",
    "RomSection",
    "RomVectors",
    "RomSram",
    "RomHardware",
    "BuildSection",
    "DebuggerSection",
    "DataDef",
    "DataSection",
    "EncodingSection",
    "PointersSection",
    "RelocationSection",
    "TABLE_TYPES",
    "COMPRESSION_TYPES",
    "load_project",
    "load_datadef",
    "load_datadefs",
    "datadef_from_dict",
    "parse_size",
    "parse_snes_addr",
    "mapping_to_address_type",
]

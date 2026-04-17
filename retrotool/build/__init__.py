"""retrotool.build — unified build pipeline (MBXML + project.toml front-ends).

Canonical in-memory form: `BuildSpec` (see `spec`).
Front-ends: `front_ends.mbxml` (MBuild 1.29 compatible), `front_ends.project_toml` (TBD).
"""
from retrotool.build.spec import (
    BuildSpec,
    Section,
    SectionKind,
    OP_REPLACE,
    OP_INSERT,
)
from retrotool.build.driver import BuildResult, SectionResult, build
from retrotool.build.diff import DiffError, DiffResult, apply_ips, write_diff, write_ips, write_xdelta, xdelta_available
from retrotool.build.extract import ExtractedSection, ExtractResult, extract
from retrotool.build.handlers import HandlerError
from retrotool.build.front_ends.mbxml import (
    MBXMLDeprecationWarning,
    migrate_mbxml,
    migrate_mbxml_string,
    parse_mbxml,
    parse_mbxml_string,
)
from retrotool.build.front_ends.project_toml import (
    parse_project_toml,
    parse_project_toml_dict,
)
from retrotool.build.resolve import (
    apply_datadefs_to_spec,
    merge_sections,
    sections_from_datadefs,
)
from retrotool.build import overflow

__all__ = [
    "BuildSpec",
    "Section",
    "SectionKind",
    "OP_REPLACE",
    "OP_INSERT",
    "MBXMLDeprecationWarning",
    "BuildResult",
    "SectionResult",
    "ExtractResult",
    "ExtractedSection",
    "DiffError",
    "DiffResult",
    "HandlerError",
    "apply_ips",
    "build",
    "extract",
    "write_diff",
    "write_ips",
    "write_xdelta",
    "xdelta_available",
    "parse_mbxml",
    "parse_mbxml_string",
    "migrate_mbxml",
    "migrate_mbxml_string",
    "parse_project_toml",
    "parse_project_toml_dict",
    "apply_datadefs_to_spec",
    "merge_sections",
    "sections_from_datadefs",
    "overflow",
]

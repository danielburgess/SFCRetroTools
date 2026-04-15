"""retrotool.mbuild — unified build pipeline (MBXML + project.toml front-ends).

Canonical in-memory form: `BuildSpec` (see `spec`).
Front-ends: `front_ends.mbxml` (MBuild 1.29 compatible), `front_ends.project_toml` (TBD).
"""
from retrotool.mbuild.spec import (
    BuildSpec,
    Section,
    SectionKind,
    OP_REPLACE,
    OP_INSERT,
)
from retrotool.mbuild.build import BuildResult, SectionResult, build
from retrotool.mbuild.diff import DiffError, DiffResult, apply_ips, write_diff, write_ips, write_xdelta, xdelta_available
from retrotool.mbuild.extract import ExtractedSection, ExtractResult, extract
from retrotool.mbuild.handlers import HandlerError
from retrotool.mbuild.front_ends.mbxml import (
    MBXMLDeprecationWarning,
    migrate_mbxml,
    migrate_mbxml_string,
    parse_mbxml,
    parse_mbxml_string,
)
from retrotool.mbuild.front_ends.project_toml import (
    parse_project_toml,
    parse_project_toml_dict,
)
from retrotool.mbuild import overflow

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
    "overflow",
]

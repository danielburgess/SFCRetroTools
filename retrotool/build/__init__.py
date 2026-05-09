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
from retrotool.build.script_filter import (
    IndexRange,
    ScriptFilter,
    ScriptTarget,
    parse_only_args,
    parse_only_token,
)
from retrotool.build.project import (
    build_libsfx_project,
    build_project,
    clean_libsfx_project,
    default_cache_dir,
    default_output_path,
    extract_project,
    info_libsfx_project,
    iter_step_builds,
    load_spec,
    make_overwrite_confirmer,
    migrate_project,
    parse_csv_set,
    parse_defines,
    resolve_extract_dest,
    resolve_jobs,
    resolve_spec_path,
    scaffold_libsfx_project,
    workers_for_print,
)
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
    "IndexRange",
    "ScriptFilter",
    "ScriptTarget",
    "parse_only_args",
    "parse_only_token",
    "build_libsfx_project",
    "build_project",
    "clean_libsfx_project",
    "default_cache_dir",
    "default_output_path",
    "extract_project",
    "info_libsfx_project",
    "iter_step_builds",
    "load_spec",
    "make_overwrite_confirmer",
    "migrate_project",
    "parse_csv_set",
    "parse_defines",
    "resolve_extract_dest",
    "resolve_jobs",
    "resolve_spec_path",
    "scaffold_libsfx_project",
    "workers_for_print",
]

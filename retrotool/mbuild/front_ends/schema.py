"""Per-element attribute validation. Handroll — no XSD dependency.

Catch typos, wrong types, unknown attrs (strict mode) before handlers run."""
from __future__ import annotations

from dataclasses import dataclass
from retrotool.mbuild.spec import SectionKind


# Attributes on <build> root.
BUILD_ATTRS = frozenset({
    "original", "name", "version", "revision", "revbyteloc",
    "path", "pad", "diff",
})


@dataclass(frozen=True)
class AttrSpec:
    required: frozenset[str]
    optional: frozenset[str]

    @property
    def allowed(self) -> frozenset[str]:
        return self.required | self.optional


# Per-kind attribute specs. Shared element attrs (kept for future extensions) are
# listed under optional to allow gradual adoption.
_SHARED_EXT = frozenset({"if", "grow", "dedupe", "pad-to"})

SECTION_ATTRS: dict[SectionKind, AttrSpec] = {
    SectionKind.REP: AttrSpec(
        required=frozenset({"file", "offset"}),
        optional=frozenset() | _SHARED_EXT,
    ),
    SectionKind.INS: AttrSpec(
        required=frozenset({"file", "offset"}),
        optional=frozenset() | _SHARED_EXT,
    ),
    SectionKind.LZR: AttrSpec(
        required=frozenset({"file", "offset"}),
        optional=frozenset({"lztype"}) | _SHARED_EXT,
    ),
    SectionKind.LZI: AttrSpec(
        required=frozenset({"file", "offset"}),
        optional=frozenset({"lztype"}) | _SHARED_EXT,
    ),
    SectionKind.RLR: AttrSpec(
        required=frozenset({"file", "offset"}),
        optional=frozenset({"rletype"}) | _SHARED_EXT,
    ),
    SectionKind.RLI: AttrSpec(
        required=frozenset({"file", "offset"}),
        optional=frozenset({"rletype"}) | _SHARED_EXT,
    ),
    SectionKind.BPR: AttrSpec(
        required=frozenset({"file", "offset"}),
        optional=frozenset({"bptype"}) | _SHARED_EXT,
    ),
    SectionKind.BPI: AttrSpec(
        required=frozenset({"file", "offset"}),
        optional=frozenset({"bptype"}) | _SHARED_EXT,
    ),
    SectionKind.SBR: AttrSpec(
        required=frozenset({"file", "offset", "table"}),
        optional=frozenset() | _SHARED_EXT,
    ),
    SectionKind.SBI: AttrSpec(
        required=frozenset({"file", "offset", "table"}),
        optional=frozenset() | _SHARED_EXT,
    ),
    # retrotool extensions
    SectionKind.BIN: AttrSpec(
        required=frozenset({"offset"}),
        optional=frozenset({"file", "size", "format"}) | _SHARED_EXT,
    ),
    SectionKind.ASAR: AttrSpec(
        required=frozenset({"file"}),
        optional=frozenset({"includes", "defines"}),
    ),
    SectionKind.GRAPHICS: AttrSpec(
        required=frozenset({"offset", "file"}),
        optional=frozenset({
            "bpp", "count", "format", "palette-ref", "tiles-per-row",
            "encode", "compression",
        }) | _SHARED_EXT,
    ),
    SectionKind.SCRIPT: AttrSpec(
        required=frozenset({"offset", "file"}),
        optional=frozenset({
            "pointer-table", "table-file", "terminator", "decoding",
            "relocation", "dte-table",
        }) | _SHARED_EXT,
    ),
    SectionKind.PROJECT: AttrSpec(
        required=frozenset({"src"}),
        optional=frozenset(),
    ),
    SectionKind.ASARDEF: AttrSpec(
        required=frozenset({"file"}),
        optional=frozenset({"datadef"}),
    ),
    SectionKind.LIBSFX: AttrSpec(
        required=frozenset({"src"}),
        optional=frozenset({"debug", "out", "stack-size"}),
    ),
    SectionKind.FIXED_RECORDS: AttrSpec(
        required=frozenset({"offset", "file"}),
        optional=frozenset({"stride", "count", "fields", "size"}) | _SHARED_EXT,
    ),
}


class SchemaError(ValueError):
    pass


def validate_build_attrs(attrs: dict, *, strict: bool = False, source: str = "") -> None:
    unknown = set(attrs) - BUILD_ATTRS
    if unknown and strict:
        raise SchemaError(f"{source}: unknown <build> attrs: {sorted(unknown)}")


def validate_section_attrs(kind: SectionKind, attrs: dict, *, strict: bool = False, source: str = "") -> None:
    spec = SECTION_ATTRS.get(kind)
    if spec is None:
        raise SchemaError(f"{source}: unhandled element <{kind.value}>")
    missing = spec.required - set(attrs)
    if missing:
        raise SchemaError(f"{source}: <{kind.value}> missing required attrs: {sorted(missing)}")
    if strict:
        unknown = set(attrs) - spec.allowed
        if unknown:
            raise SchemaError(
                f"{source}: <{kind.value}> unknown attrs: {sorted(unknown)}"
            )

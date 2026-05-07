"""MBXML front-end. MBuild 1.29 compatible + retrotool extensions.

Parses `.mbxml` / `.xml` files to `BuildSpec`. Uses stdlib `xml.etree`.
Offsets accepted as hex (with or without `0x` / `$` prefix) or decimal.
File paths with Windows separators are normalized to POSIX in-memory.
"""
from __future__ import annotations

import warnings
import xml.etree.ElementTree as ET  # types only (Element, Comment, tostring)
import defusedxml.ElementTree as DET  # parsing — rejects entity-expansion attacks
from pathlib import Path, PurePosixPath
from typing import Literal, Optional

from retrotool.build.spec import BuildSpec, Section, SectionKind
from retrotool.build.front_ends.schema import (
    SchemaError,
    validate_build_attrs,
    validate_section_attrs,
)
from retrotool.build.interpolate import build_vars, interpolate_attrs


class MBXMLDeprecationWarning(DeprecationWarning):
    """Emitted when an MBXML element kept for MBuild 1.29 backward compat is used.

    Filter with `warnings.simplefilter('ignore', MBXMLDeprecationWarning)`."""


# Per plan §3 "Unified <compression> attribute": MBuild's codec-x-operation matrix
# (lzr/lzi/rlr/rli/bpr/bpi/sbr/sbi) collapses to a data element + codec= + grow=.
# Map each legacy kind → suggested replacement (templated with the codec attr name).
_LEGACY_REPLACEMENTS: dict[SectionKind, str] = {
    SectionKind.LZR: '<bin codec="<lztype>" grow="replace" file=… offset=…/>',
    SectionKind.LZI: '<bin codec="<lztype>" grow="insert"  file=… offset=…/>',
    SectionKind.RLR: '<bin codec="<rletype>" grow="replace" file=… offset=…/>',
    SectionKind.RLI: '<bin codec="<rletype>" grow="insert"  file=… offset=…/>',
    SectionKind.BPR: '<graphics encode="<bptype>" grow="replace" file=… offset=…/>',
    SectionKind.BPI: '<graphics encode="<bptype>" grow="insert"  file=… offset=…/>',
    SectionKind.SBR: '<script grow="replace" table-file=… file=… offset=…/>',
    SectionKind.SBI: '<script grow="insert"  table-file=… file=… offset=…/>',
}

DeprecationMode = Literal["warn", "error", "ignore"]


def _emit_deprecation(kind: SectionKind, source: str, mode: DeprecationMode) -> None:
    if mode == "ignore":
        return
    replacement = _LEGACY_REPLACEMENTS.get(kind)
    if replacement is None:
        return
    msg = (
        f"{source}: <{kind.value}> is a MBuild 1.29 legacy element. "
        f"Prefer unified style: {replacement}"
    )
    if mode == "error":
        raise SchemaError(msg)
    warnings.warn(msg, MBXMLDeprecationWarning, stacklevel=3)


_TRUTHY = {"true", "1", "yes", "on"}
_FALSY = {"false", "0", "no", "off"}


def _parse_bool(v: Optional[str]) -> bool:
    return v is not None and v.strip().lower() in _TRUTHY


def _parse_tristate_bool(v: Optional[str]) -> Optional[bool]:
    """Three-state parse: missing → None, truthy → True, falsy → False.

    Used for attrs where the absence of the key must be distinguishable
    from an explicit false (e.g. per-section cache override)."""
    if v is None:
        return None
    s = v.strip().lower()
    if not s:
        return None
    if s in _TRUTHY:
        return True
    if s in _FALSY:
        return False
    return None


def _parse_offset(v: Optional[str]) -> Optional[int]:
    if v is None:
        return None
    s = v.strip().replace("_", "")
    if not s:
        return None
    if s.startswith("$"):
        return int(s[1:], 16)
    if s.lower().startswith("0x"):
        return int(s, 16)
    # MBuild convention: offset attrs are raw hex, no prefix.
    try:
        return int(s, 16)
    except ValueError:
        return int(s, 10)


def _parse_int(v: Optional[str]) -> Optional[int]:
    if v is None:
        return None
    s = v.strip().replace("_", "")
    if s.startswith("$"):
        return int(s[1:], 16)
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(s)


def _parse_jobs(v: Optional[str]) -> Optional[int]:
    """Parse a jobs= attribute. None → None; "auto" → 0; non-negative int
    → int. 0 resolves to os.cpu_count() at run time."""
    if v is None:
        return None
    if v.strip().lower() == "auto":
        return 0
    n = _parse_int(v)
    if n is None or n < 0:
        raise SchemaError(f"<build> jobs= must be a non-negative int or 'auto', got {v!r}")
    return n


def _norm_path(v: str) -> PurePosixPath:
    return PurePosixPath(v.replace("\\", "/"))


def _norm_paths(v: str) -> list[PurePosixPath]:
    # MBuild `<ins file="A|B|C"/>` concatenates the listed files.
    return [_norm_path(p) for p in v.split("|") if p]


# Maps each legacy kind to (target_kind, grow_value). Codec value comes from the
# legacy element's lztype/rletype/bptype attr at parse time.
_LEGACY_MIGRATION: dict[SectionKind, tuple[SectionKind, str]] = {
    SectionKind.LZR: (SectionKind.BIN, "replace"),
    SectionKind.LZI: (SectionKind.BIN, "insert"),
    SectionKind.RLR: (SectionKind.BIN, "replace"),
    SectionKind.RLI: (SectionKind.BIN, "insert"),
    SectionKind.BPR: (SectionKind.GRAPHICS, "replace"),
    SectionKind.BPI: (SectionKind.GRAPHICS, "insert"),
    SectionKind.SBR: (SectionKind.SCRIPT, "replace"),
    SectionKind.SBI: (SectionKind.SCRIPT, "insert"),
}


def parse_mbxml(
    path: Path | str,
    *,
    strict: bool = False,
    deprecations: DeprecationMode = "warn",
    defines: Optional[dict[str, str]] = None,
) -> BuildSpec:
    path = Path(path)
    tree = DET.parse(path)
    return _build_from_root(
        tree.getroot(), strict=strict, source_path=path,
        deprecations=deprecations, defines=defines,
    )


def parse_mbxml_string(
    text: str,
    *,
    strict: bool = False,
    source: str = "<string>",
    deprecations: DeprecationMode = "warn",
    defines: Optional[dict[str, str]] = None,
) -> BuildSpec:
    root = DET.fromstring(text)
    return _build_from_root(
        root, strict=strict, source_path=Path(source),
        deprecations=deprecations, defines=defines,
    )


class IncludeError(SchemaError):
    """Raised when an `<include>` cannot be resolved or loops back on itself."""


def _resolve_include(
    src: str, *, parent: Path, strict: bool, deprecations: DeprecationMode,
    vars: dict[str, str], seen: set[Path],
) -> list[Section]:
    """Parse an included file and return its `<build>` children as Sections.

    `seen` tracks resolved-include paths to break cycles. Defines from the
    parent scope are forwarded so the included file sees the same vars."""
    inc_path = (parent.parent / src).resolve()
    if inc_path in seen:
        chain = " -> ".join(str(p) for p in seen) + f" -> {inc_path}"
        raise IncludeError(f"include cycle detected: {chain}")
    if not inc_path.exists():
        raise IncludeError(f"{parent}: <include> src not found: {inc_path}")
    seen = seen | {inc_path}

    tree = DET.parse(inc_path)
    root = tree.getroot()
    if root.tag != "build":
        raise IncludeError(f"{inc_path}: included file root must be <build>")

    # Included file's own <build> attrs do not override parent. We only use
    # them to seed locals so an included file can self-reference its own
    # name/version if those weren't supplied by the parent.
    inc_attrs = dict(root.attrib)
    local_vars = {**inc_attrs, **vars}  # parent vars win

    sections: list[Section] = []
    for child in root:
        if child.tag is ET.Comment:
            continue
        if child.tag == "include":
            sections.extend(_resolve_include(
                child.attrib["src"], parent=inc_path, strict=strict,
                deprecations=deprecations, vars=local_vars, seen=seen,
            ))
            continue
        sections.append(_section_from_element(
            child, strict=strict, source_path=inc_path,
            deprecations=deprecations, vars=local_vars,
        ))
    return sections


def _build_from_root(
    root: ET.Element, *, strict: bool, source_path: Path,
    deprecations: DeprecationMode, defines: Optional[dict[str, str]] = None,
) -> BuildSpec:
    if root.tag != "build":
        raise SchemaError(f"{source_path}: root element must be <build>, got <{root.tag}>")
    raw_attrs = dict(root.attrib)
    # Build the vars table first so <build>'s own attrs can reference
    # user-supplied defines (e.g. revision="${rev}").
    vars = build_vars(raw_attrs, defines)
    attrs = interpolate_attrs(raw_attrs, vars, source=str(source_path))
    validate_build_attrs(attrs, strict=strict, source=str(source_path))

    spec = BuildSpec(
        original=_norm_path(attrs["original"]) if "original" in attrs else None,
        name=attrs.get("name"),
        version=attrs.get("version"),
        revision=attrs.get("revision"),
        revbyteloc=_parse_offset(attrs.get("revbyteloc")),
        path=_norm_path(attrs["path"]) if "path" in attrs else None,
        pad=_parse_bool(attrs.get("pad")),
        diff=attrs.get("diff"),
        source_path=PurePosixPath(source_path.as_posix()),
        vars=vars,
        jobs=_parse_jobs(attrs.get("jobs")),
    )

    for child in root:
        if child.tag is ET.Comment:
            continue
        if child.tag == "include":
            src = child.attrib.get("src")
            if not src:
                raise IncludeError(f"{source_path}: <include> requires src=")
            spec.sections.extend(_resolve_include(
                src, parent=source_path, strict=strict,
                deprecations=deprecations, vars=vars, seen={source_path.resolve()},
            ))
            continue
        if child.tag == "freespace":
            child_attrs = interpolate_attrs(
                dict(child.attrib), vars, source=str(source_path)
            )
            lo = _parse_offset(child_attrs.get("lo"))
            hi = _parse_offset(child_attrs.get("hi"))
            if lo is None or hi is None or hi <= lo:
                raise SchemaError(
                    f"{source_path}: <freespace> requires lo= and hi= with hi > lo"
                )
            spec.freespace.append((lo, hi))
            continue
        if child.tag == "label":
            child_attrs = interpolate_attrs(
                dict(child.attrib), vars, source=str(source_path)
            )
            name = child_attrs.get("name")
            at = _parse_offset(child_attrs.get("at"))
            if not name or at is None:
                raise SchemaError(
                    f"{source_path}: <label> requires name= and at="
                )
            spec.labels[name] = at
            continue
        spec.sections.append(
            _section_from_element(
                child, strict=strict, source_path=source_path,
                deprecations=deprecations, vars=vars,
            )
        )
    return spec


def _section_from_element(
    el: ET.Element, *, strict: bool, source_path: Path,
    deprecations: DeprecationMode, vars: dict[str, str],
) -> Section:
    try:
        parsed_kind = SectionKind(el.tag)
    except ValueError as e:
        raise SchemaError(f"{source_path}: unknown element <{el.tag}>") from e
    raw_attrs = dict(el.attrib)
    # `if=` is interpolated lazily during evaluation (so its operands can
    # reference the same vars). Strip it before per-attr interpolation so
    # the literal expression survives.
    cond = raw_attrs.pop("if", None)
    attrs = interpolate_attrs(raw_attrs, vars, source=str(source_path))
    if cond is not None:
        attrs["if"] = cond
    source = f"{source_path}"
    validate_section_attrs(parsed_kind, attrs, strict=strict, source=source)

    # Handle MBuild 1.29 legacy elements: warn (or error) and remap to unified kind.
    original_kind: Optional[SectionKind] = None
    kind = parsed_kind
    grow_override: Optional[str] = None
    if parsed_kind in _LEGACY_MIGRATION:
        _emit_deprecation(parsed_kind, source, deprecations)
        kind, grow_override = _LEGACY_MIGRATION[parsed_kind]
        original_kind = parsed_kind

    files: list[PurePosixPath] = []
    if "file" in attrs:
        files = _norm_paths(attrs["file"])
    elif "src" in attrs:
        files = [_norm_path(attrs["src"])]

    codec = attrs.get("lztype") or attrs.get("rletype") or attrs.get("bptype") or attrs.get("encode")
    # Schema permits both `table` (TOML canonical) and `table-file` (legacy
    # mbxml). Prefer `table`; fall back to `table-file`.
    _table_raw = attrs.get("table") or attrs.get("table-file")
    table = _norm_path(_table_raw) if _table_raw else None

    return Section(
        kind=kind,
        offset=_parse_offset(attrs.get("offset")),
        files=files,
        codec=codec,
        table=table,
        size=_parse_int(attrs.get("size")),
        bpp=_parse_int(attrs.get("bpp")),
        count=_parse_int(attrs.get("count")),
        pointer_table=_parse_offset(attrs.get("pointer-table")),
        pad_to=_parse_int(attrs.get("pad-to")),
        grow=grow_override or attrs.get("grow"),
        dedupe=_parse_bool(attrs.get("dedupe")),
        stride=_parse_int(attrs.get("stride")),
        condition=attrs.get("if"),
        cache=_parse_tristate_bool(attrs.get("cache")),
        attrs=attrs,
        source=source,
        original_kind=original_kind,
    )


# ---- on-disk migration -----------------------------------------------------

# Default codec names assumed when MBuild element omits the type attribute.
_DEFAULT_CODECS: dict[SectionKind, tuple[str, str]] = {
    # (target_element, default_codec_value_if_missing)
    SectionKind.LZR: ("bin", "lzss-legacy"),
    SectionKind.LZI: ("bin", "lzss-legacy"),
    SectionKind.RLR: ("bin", "rle"),
    SectionKind.RLI: ("bin", "rle"),
    SectionKind.BPR: ("graphics", "2bpp-to-1bpp-il"),
    SectionKind.BPI: ("graphics", "2bpp-to-1bpp-il"),
    SectionKind.SBR: ("script", ""),
    SectionKind.SBI: ("script", ""),
}


def migrate_mbxml_string(text: str) -> str:
    """Return `text` with all legacy MBuild 1.29 elements rewritten to unified form.

    Round-trip safe — unknown elements untouched."""
    root = DET.fromstring(text)
    _migrate_tree(root)
    # ET.tostring drops the original XML declaration; preserve the visible content.
    return ET.tostring(root, encoding="unicode")


def migrate_mbxml(path: Path | str, *, in_place: bool = False) -> str:
    """Migrate an .mbxml file to unified form. Returns rewritten text. If
    `in_place=True`, also writes back to `path` (with a `.bak` sibling)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    new_text = migrate_mbxml_string(text)
    if in_place:
        path.with_suffix(path.suffix + ".bak").write_text(text, encoding="utf-8")
        path.write_text(new_text, encoding="utf-8")
    return new_text


def _migrate_tree(root: ET.Element) -> None:
    for el in list(root):
        try:
            kind = SectionKind(el.tag)
        except ValueError:
            continue
        if kind not in _LEGACY_MIGRATION:
            continue
        target_tag, default_codec = _DEFAULT_CODECS[kind]
        _, grow = _LEGACY_MIGRATION[kind]
        codec_attr = el.get("lztype") or el.get("rletype") or el.get("bptype") or default_codec

        el.tag = target_tag
        # Drop the legacy codec attrs.
        for legacy in ("lztype", "rletype", "bptype"):
            if legacy in el.attrib:
                del el.attrib[legacy]
        if codec_attr:
            if target_tag == "graphics":
                el.set("encode", codec_attr)
            else:
                el.set("codec", codec_attr)
        el.set("grow", grow)

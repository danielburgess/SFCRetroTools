"""`project.toml` front-end → `BuildSpec`.

Accepts any TOML file containing an `[mbuild]` table. Downstream handlers,
`build()`, and `extract()` are identical between the two front-ends.

Minimal example::

    [mbuild]
    original = "base.sfc"
    name = "Demo"
    pad = true
    revbyteloc = 0x7FDB
    revision = "01"

    [[mbuild.sections]]
    kind = "rep"
    offset = 0x100
    file = "patch.bin"

    [[mbuild.sections]]
    kind = "bin"
    offset = 0x200000
    file = ["chunk_a.bin", "chunk_b.bin"]
    codec = "lzss-zamn"
    grow = "replace"

TOML values may be plain integers, or hex strings (`"0x100"`, `"$1B:8000"`,
`"11E3"` — MBuild's raw-hex convention is honored). `file` may be a string or
a list of strings.
"""
from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from retrotool.mbuild.spec import BuildSpec, Section, SectionKind
from retrotool.mbuild.front_ends.schema import SchemaError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - project requires 3.12
    import tomli as tomllib  # type: ignore


# ---- value coercion -------------------------------------------------------

def _coerce_offset(v: Any, field: str) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip().replace("_", "")
        if not s:
            return None
        if s.startswith("$"):
            return int(s.replace(":", "").replace("$", ""), 16)
        if s.lower().startswith("0x"):
            return int(s, 16)
        try:
            return int(s, 16)
        except ValueError:
            return int(s, 10)
    raise SchemaError(f"{field}: expected int or hex string, got {type(v).__name__}")


def _coerce_int(v: Any, field: str) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip().replace("_", "")
        if s.startswith("$"):
            return int(s[1:], 16)
        if s.lower().startswith("0x"):
            return int(s, 16)
        return int(s)
    raise SchemaError(f"{field}: expected int, got {type(v).__name__}")


def _coerce_path(v: Any, field: str) -> PurePosixPath:
    if not isinstance(v, str):
        raise SchemaError(f"{field}: expected string path, got {type(v).__name__}")
    return PurePosixPath(v.replace("\\", "/"))


def _coerce_files(v: Any, field: str) -> list[PurePosixPath]:
    if v is None:
        return []
    if isinstance(v, str):
        return [_coerce_path(v, field)]
    if isinstance(v, list):
        return [_coerce_path(item, f"{field}[{i}]") for i, item in enumerate(v)]
    raise SchemaError(f"{field}: expected string or list of strings")


# ---- main -----------------------------------------------------------------

_BUILD_SCALAR_FIELDS = {
    "original", "name", "version", "revision", "path", "diff",
}


def parse_project_toml(path: Path | str) -> BuildSpec:
    path = Path(path).resolve()
    return _parse_project_toml(path, _seen=set())


def _parse_project_toml(path: Path, *, _seen: set[Path]) -> BuildSpec:
    if path in _seen:
        raise SchemaError(f"include cycle detected at {path}")
    _seen = _seen | {path}
    with path.open("rb") as f:
        data = tomllib.load(f)
    return parse_project_toml_dict(data, source_path=path, _seen=_seen)


def parse_project_toml_dict(
    data: dict,
    *,
    source_path: Optional[Path] = None,
    _seen: Optional[set[Path]] = None,
) -> BuildSpec:
    if "mbuild" not in data:
        raise SchemaError(
            f"{source_path or '<dict>'}: project TOML has no [mbuild] table"
        )
    mb = data["mbuild"]
    if not isinstance(mb, dict):
        raise SchemaError("[mbuild] must be a table")

    source = str(source_path) if source_path else "<toml>"
    spec = BuildSpec(
        original=_coerce_path(mb["original"], "[mbuild].original") if "original" in mb else None,
        name=mb.get("name"),
        version=mb.get("version"),
        revision=mb.get("revision"),
        revbyteloc=_coerce_offset(mb.get("revbyteloc"), "[mbuild].revbyteloc"),
        path=_coerce_path(mb["path"], "[mbuild].path") if "path" in mb else None,
        pad=bool(mb.get("pad", False)),
        diff=mb.get("diff"),
        source_path=PurePosixPath(source_path.as_posix()) if source_path else None,
    )

    fs = mb.get("freespace", [])
    if fs:
        if not isinstance(fs, list):
            raise SchemaError("[mbuild].freespace must be a list of [lo, hi] pairs")
        for i, pair in enumerate(fs):
            if not (isinstance(pair, list) and len(pair) == 2):
                raise SchemaError(f"[mbuild].freespace[{i}] must be [lo, hi]")
            lo = _coerce_offset(pair[0], f"[mbuild].freespace[{i}][0]")
            hi = _coerce_offset(pair[1], f"[mbuild].freespace[{i}][1]")
            if lo is None or hi is None or hi <= lo:
                raise SchemaError(f"[mbuild].freespace[{i}] invalid range {pair!r}")
            spec.freespace.append((lo, hi))

    raw_labels = mb.get("labels", [])
    if raw_labels:
        if not isinstance(raw_labels, list):
            raise SchemaError("[[mbuild.labels]] must be an array of tables")
        for i, lab in enumerate(raw_labels):
            if not isinstance(lab, dict):
                raise SchemaError(f"[[mbuild.labels]][{i}] must be a table")
            name = lab.get("name")
            if not isinstance(name, str) or not name:
                raise SchemaError(f"[[mbuild.labels]][{i}] missing name=")
            at = _coerce_offset(lab.get("at"), f"[[mbuild.labels]][{i}].at")
            if at is None:
                raise SchemaError(f"[[mbuild.labels]][{i}] missing at=")
            spec.labels[name] = at

    raw_sections = mb.get("sections", [])
    if not isinstance(raw_sections, list):
        raise SchemaError("[[mbuild.sections]] must be an array of tables")

    for i, entry in enumerate(raw_sections):
        if not isinstance(entry, dict):
            raise SchemaError(f"[[mbuild.sections]][{i}] must be a table")
        spec.sections.append(_section_from_dict(entry, index=i, source=source))

    # `[mbuild].include = ["tables/foo.toml", …]` splices sections from sibling
    # TOML files. Each included file must itself contain a [mbuild] table; only
    # the sections array is merged (parent header wins for original/name/etc).
    includes = mb.get("include", [])
    if includes:
        if not isinstance(includes, list):
            raise SchemaError("[mbuild].include must be a list of paths")
        if source_path is None:
            raise SchemaError(
                "[mbuild].include cannot be used when parsing from a dict "
                "without source_path (paths can't be resolved)"
            )
        base = source_path.parent
        seen = _seen if _seen is not None else {source_path.resolve()}
        for inc in includes:
            if not isinstance(inc, str):
                raise SchemaError(f"[mbuild].include entry not a string: {inc!r}")
            inc_path = (base / inc).resolve()
            if not inc_path.exists():
                raise SchemaError(f"include not found: {inc_path}")
            sub = _parse_project_toml(inc_path, _seen=seen)
            spec.sections.extend(sub.sections)

    return spec


def _section_from_dict(entry: dict, *, index: int, source: str) -> Section:
    kind_str = entry.get("kind")
    if not kind_str:
        raise SchemaError(f"{source}: [[mbuild.sections]][{index}] missing kind=")
    try:
        kind = SectionKind(kind_str)
    except ValueError as e:
        raise SchemaError(f"{source}: unknown kind={kind_str!r} at sections[{index}]") from e

    field_prefix = f"[[mbuild.sections]][{index}]"
    files = _coerce_files(entry.get("file"), f"{field_prefix}.file")
    # Also accept `src` for element-parity with MBXML <libsfx src=…>.
    if not files and "src" in entry:
        files = _coerce_files(entry["src"], f"{field_prefix}.src")
    table = (
        _coerce_path(entry["table"], f"{field_prefix}.table")
        if "table" in entry else None
    )

    offset = _coerce_offset(entry.get("offset"), f"{field_prefix}.offset")
    codec = entry.get("codec")
    grow = entry.get("grow")

    fallback_table = (
        _coerce_path(entry["fallback-table"], f"{field_prefix}.fallback-table")
        if "fallback-table" in entry else None
    )
    word_wrap = entry.get("word-wrap")
    if word_wrap is not None:
        if not isinstance(word_wrap, dict):
            raise SchemaError(f"{field_prefix}.word-wrap must be a table")
        ww = {}
        if "line-width" in word_wrap:
            ww["line_width"] = _coerce_int(word_wrap["line-width"], f"{field_prefix}.word-wrap.line-width")
        if "max-lines" in word_wrap:
            ww["max_lines"] = _coerce_int(word_wrap["max-lines"], f"{field_prefix}.word-wrap.max-lines")
        if "entries" in word_wrap:
            ww["entries"] = word_wrap["entries"]
        word_wrap = ww
    overflow = entry.get("overflow")
    if overflow is not None and not isinstance(overflow, dict):
        raise SchemaError(f"{field_prefix}.overflow must be a table")

    return Section(
        kind=kind,
        offset=offset,
        files=files,
        codec=codec,
        table=table,
        size=_coerce_int(entry.get("size"), f"{field_prefix}.size"),
        bpp=_coerce_int(entry.get("bpp"), f"{field_prefix}.bpp"),
        count=_coerce_int(entry.get("count"), f"{field_prefix}.count"),
        stride=_coerce_int(entry.get("stride"), f"{field_prefix}.stride"),
        pointer_table=_coerce_offset(entry.get("pointer-table"), f"{field_prefix}.pointer-table"),
        pad_to=_coerce_int(entry.get("pad-to"), f"{field_prefix}.pad-to"),
        grow=grow,
        dedupe=bool(entry.get("dedupe", False)),
        condition=entry.get("if"),
        pointer_size=_coerce_int(entry.get("pointer-size"), f"{field_prefix}.pointer-size"),
        terminator=_coerce_int(entry.get("terminator"), f"{field_prefix}.terminator"),
        fallback_table=fallback_table,
        word_wrap=word_wrap,
        textbuf_limit=_coerce_int(entry.get("textbuf-limit"), f"{field_prefix}.textbuf-limit"),
        overflow=overflow,
        attrs={k: str(v) for k, v in entry.items()},
        source=f"{source}:sections[{index}]",
        original_kind=None,
    )

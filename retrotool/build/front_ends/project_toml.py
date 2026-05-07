"""`project.toml` front-end → `BuildSpec`.

Reads the `[rom.build]` pipeline table. The source rom name + project name
are pulled from `[rom]` (`file` → `original`, `name` → `name`) — they must
not be redeclared in `[rom.build]`. Downstream handlers, `build()`, and
`extract()` are identical between the two front-ends.

Minimal example::

    [rom]
    name = "Demo"
    file = "base.sfc"
    mapping = "lorom"
    size = "2M"

    [rom.build]
    pad = true
    revbyteloc = 0x7FDB
    revision = "01"

    [[rom.build.sections]]
    kind = "rep"
    offset = 0x100
    file = "patch.bin"

    [[rom.build.sections]]
    kind = "bin"
    offset = 0x200000
    file = ["chunk_a.bin", "chunk_b.bin"]
    codec = "lzss-zamn"
    grow = "replace"

Included fragments (`[rom.build].include = [...]`) contain `[rom.build]`
only — no `[rom]` header. Parent wins for `original`/`name`/etc.

TOML values may be plain integers, or hex strings (`"0x100"`, `"$1B:8000"`,
`"11E3"` — MBuild's raw-hex convention is honored). `file` may be a string or
a list of strings.
"""
from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from retrotool.build.spec import BuildSpec, Section, SectionKind
from retrotool.build.front_ends.schema import SchemaError
from retrotool.build.interpolate import build_vars, interpolate

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


_TRUTHY_STR = {"true", "1", "yes", "on"}
_FALSY_STR = {"false", "0", "no", "off"}


def _coerce_tristate_bool(v: Any, field: str) -> Optional[bool]:
    """None/missing → None; bool → bool; truthy/falsy str or int → bool.
    Used for attrs where absence must be distinguishable from explicit false."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if not s:
            return None
        if s in _TRUTHY_STR:
            return True
        if s in _FALSY_STR:
            return False
        raise SchemaError(f"{field}: expected bool-like, got {v!r}")
    raise SchemaError(f"{field}: expected bool-like, got {type(v).__name__}")


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


def parse_project_toml(
    path: Path | str,
    *,
    defines: Optional[dict[str, str]] = None,
) -> BuildSpec:
    path = Path(path).resolve()
    return _parse_project_toml(path, _seen=set(), defines=defines)


def _parse_project_toml(
    path: Path, *, _seen: set[Path],
    defines: Optional[dict[str, str]] = None,
) -> BuildSpec:
    if path in _seen:
        raise SchemaError(f"include cycle detected at {path}")
    _seen = _seen | {path}
    with path.open("rb") as f:
        data = tomllib.load(f)
    return parse_project_toml_dict(
        data, source_path=path, _seen=_seen, defines=defines,
    )


def _interpolate_tree(v: Any, vars: dict[str, str], *, source: str) -> Any:
    """Recursively apply `${var}` substitution to every string value in `v`.

    Preserves non-string scalars (int, bool) and container shapes. `if=`
    expressions are left raw (interpolation happens at evaluation time).
    """
    if isinstance(v, str):
        return interpolate(v, vars, source=source)
    if isinstance(v, dict):
        out: dict = {}
        for k, val in v.items():
            if k == "if":
                out[k] = val  # defer interpolation
            else:
                out[k] = _interpolate_tree(val, vars, source=source)
        return out
    if isinstance(v, list):
        return [_interpolate_tree(item, vars, source=source) for item in v]
    return v


def parse_project_toml_dict(
    data: dict,
    *,
    source_path: Optional[Path] = None,
    _seen: Optional[set[Path]] = None,
    defines: Optional[dict[str, str]] = None,
) -> BuildSpec:
    # Pipeline lives at [rom.build]. `original`/`name` are pulled from [rom]
    # and must not appear in [rom.build]. Fragments (used via include) are
    # permitted to omit [rom] entirely.
    rom = data.get("rom") or {}
    if not isinstance(rom, dict):
        raise SchemaError("[rom] must be a table")

    mb = rom.get("build")
    if mb is None:
        raise SchemaError(
            f"{source_path or '<dict>'}: project TOML has no [rom.build] table"
        )
    if not isinstance(mb, dict):
        raise SchemaError("[rom.build] must be a table")

    for forbidden in ("original", "name"):
        if forbidden in mb:
            raise SchemaError(
                f"[rom.build].{forbidden} is not allowed — set [rom].{'file' if forbidden == 'original' else 'name'} instead"
            )

    source = str(source_path) if source_path else "<toml>"

    # Seed vars: name from [rom], version/revision from [rom.build]. Layer
    # user defines on top, then interpolate every string in [rom.build].
    seed_attrs: dict[str, str] = {}
    if isinstance(rom.get("name"), str):
        seed_attrs["name"] = rom["name"]
    for k in ("version", "revision"):
        if isinstance(mb.get(k), str):
            seed_attrs[k] = mb[k]
    vars = build_vars(seed_attrs, defines)
    mb = _interpolate_tree(mb, vars, source=source)

    original = None
    if isinstance(rom.get("file"), str):
        original = _coerce_path(rom["file"], "[rom].file")

    spec = BuildSpec(
        original=original,
        name=rom.get("name"),
        version=mb.get("version"),
        revision=mb.get("revision"),
        revbyteloc=_coerce_offset(mb.get("revbyteloc"), "[rom.build].revbyteloc"),
        path=_coerce_path(mb["path"], "[rom.build].path") if "path" in mb else None,
        pad=bool(mb.get("pad", False)),
        pad_byte=_coerce_offset(mb.get("pad-byte", mb.get("pad_byte", 0x00)), "[rom.build].pad-byte") or 0x00,
        diff=mb.get("diff"),
        source_path=PurePosixPath(source_path.as_posix()) if source_path else None,
        vars=vars,
    )

    fs = mb.get("freespace", [])
    if fs:
        if not isinstance(fs, list):
            raise SchemaError("[rom.build].freespace must be a list of [lo, hi] pairs")
        for i, pair in enumerate(fs):
            if not (isinstance(pair, list) and len(pair) == 2):
                raise SchemaError(f"[rom.build].freespace[{i}] must be [lo, hi]")
            lo = _coerce_offset(pair[0], f"[rom.build].freespace[{i}][0]")
            hi = _coerce_offset(pair[1], f"[rom.build].freespace[{i}][1]")
            if lo is None or hi is None or hi <= lo:
                raise SchemaError(f"[rom.build].freespace[{i}] invalid range {pair!r}")
            spec.freespace.append((lo, hi))

    raw_labels = mb.get("labels", [])
    if raw_labels:
        if not isinstance(raw_labels, list):
            raise SchemaError("[[rom.build.labels]] must be an array of tables")
        for i, lab in enumerate(raw_labels):
            if not isinstance(lab, dict):
                raise SchemaError(f"[[rom.build.labels]][{i}] must be a table")
            name = lab.get("name")
            if not isinstance(name, str) or not name:
                raise SchemaError(f"[[rom.build.labels]][{i}] missing name=")
            at = _coerce_offset(lab.get("at"), f"[[rom.build.labels]][{i}].at")
            if at is None:
                raise SchemaError(f"[[rom.build.labels]][{i}] missing at=")
            spec.labels[name] = at

    # Project-level defaults for DataDef sections: [rom.build.section.overflow],
    # [rom.build.section.placement]. Inherited by every DataDef that declares a
    # `[section]` sub-table but omits the corresponding key. Inline sections
    # (`[[rom.build.sections]]`) are not affected.
    sd = mb.get("section")
    if sd is not None:
        if not isinstance(sd, dict):
            raise SchemaError("[rom.build.section] must be a table")
        spec.section_defaults = dict(sd)

    # `en_data_dir=` at the project root (outside [rom.build]) feeds the
    # file-autodefault for DataDef sections. Pulled from the top-level dict
    # here so resolver sees it without a separate ProjectConfig param.
    en_dir = data.get("en_data_dir")
    if isinstance(en_dir, str) and en_dir:
        spec.en_data_dir = en_dir

    # Generic `*_data_dir=` scalars → data_dirs_by_lang. Lets `extract --lang X`
    # target any declared language without hardcoding which one the build uses.
    for k, v in data.items():
        if isinstance(k, str) and k.endswith("_data_dir") and isinstance(v, str) and v:
            lang = k[:-len("_data_dir")].lower()
            if lang:
                spec.data_dirs_by_lang[lang] = v

    # `[extract]` table — opt-in extract-phase config. Currently supports
    # `default_lang = "xx"`; placeholder for future extract-only overrides.
    ex = data.get("extract")
    if ex is not None:
        if not isinstance(ex, dict):
            raise SchemaError("[extract] must be a table")
        spec.extract_config = dict(ex)

    # `[mesen]` table — Mesen2-emulator integration. Currently supports
    # `sync-sram = true` (default false) + optional `saves-dir = "..."`
    # override. When enabled, the build driver copies the source ROM's
    # .srm to the output ROM's .srm after writing the output (see
    # `retrotool.debugger.mesen_saves.sync_sram`).
    mesen = data.get("mesen")
    if mesen is not None:
        if not isinstance(mesen, dict):
            raise SchemaError("[mesen] must be a table")
        sync_val = mesen.get("sync-sram", mesen.get("sync_sram"))
        spec.sync_sram = bool(_coerce_tristate_bool(sync_val, "[mesen].sync-sram"))
        sdir = mesen.get("saves-dir", mesen.get("saves_dir"))
        if sdir is not None:
            if not isinstance(sdir, str):
                raise SchemaError("[mesen].saves-dir must be a string")
            spec.mesen_saves_dir = sdir
        arch_val = mesen.get("archive-overwritten",
                             mesen.get("archive_overwritten"))
        arch_b = _coerce_tristate_bool(arch_val, "[mesen].archive-overwritten")
        if arch_b is not None:
            spec.archive_sram = arch_b

    raw_order = mb.get("order")
    if raw_order is not None:
        if not isinstance(raw_order, list) or not all(isinstance(x, str) for x in raw_order):
            raise SchemaError("[rom.build].order must be a list of section names")
        spec.order = list(raw_order)

    raw_sections = mb.get("sections", [])
    if not isinstance(raw_sections, list):
        raise SchemaError("[[rom.build.sections]] must be an array of tables")

    for i, entry in enumerate(raw_sections):
        if not isinstance(entry, dict):
            raise SchemaError(f"[[rom.build.sections]][{i}] must be a table")
        spec.sections.append(_section_from_dict(entry, index=i, source=source))

    # `[rom.build].include = ["tables/foo.toml", …]` splices sections from
    # sibling TOML files. Each included file must itself contain [rom.build];
    # only sections/freespace/labels are merged (parent wins on key clash).
    includes = mb.get("include", [])
    if includes:
        if not isinstance(includes, list):
            raise SchemaError("[rom.build].include must be a list of paths")
        if source_path is None:
            raise SchemaError(
                "[rom.build].include cannot be used when parsing from a dict "
                "without source_path (paths can't be resolved)"
            )
        base = source_path.parent
        seen = _seen if _seen is not None else {source_path.resolve()}
        for inc in includes:
            if not isinstance(inc, str):
                raise SchemaError(f"[rom.build].include entry not a string: {inc!r}")
            inc_path = (base / inc).resolve()
            if not inc_path.exists():
                raise SchemaError(f"include not found: {inc_path}")
            sub = _parse_project_toml(inc_path, _seen=seen, defines=defines)
            spec.sections.extend(sub.sections)
            # Merge freespace/labels from includes (parent wins on key clash).
            spec.freespace.extend(sub.freespace)
            for k, v in sub.labels.items():
                spec.labels.setdefault(k, v)

    return spec


def _section_from_dict(entry: dict, *, index: int, source: str) -> Section:
    kind_str = entry.get("kind")
    if not kind_str:
        raise SchemaError(f"{source}: [[rom.build.sections]][{index}] missing kind=")
    try:
        kind = SectionKind(kind_str)
    except ValueError as e:
        raise SchemaError(f"{source}: unknown kind={kind_str!r} at sections[{index}]") from e

    field_prefix = f"[[rom.build.sections]][{index}]"

    if "datadef" in entry:
        raise SchemaError(
            f"{field_prefix}: `datadef=` is no longer accepted. To reference a "
            f"DataDef, add a `[section]` sub-table to its tables/<name>.toml — "
            f"it will be auto-included in the build pipeline."
        )
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
        if "newline" in word_wrap:
            ww["newline"] = str(word_wrap["newline"])
        for wm_key in ("wrap-mode", "wrap_mode"):
            if wm_key in word_wrap:
                ww["wrap_mode"] = str(word_wrap[wm_key])
                break
        for fc_key in ("fill-char", "fill_char"):
            if fc_key in word_wrap:
                ww["fill_char"] = str(word_wrap[fc_key])
                break
        word_wrap = ww
    overflow = entry.get("overflow")
    if overflow is not None and not isinstance(overflow, dict):
        raise SchemaError(f"{field_prefix}.overflow must be a table")
    placement = entry.get("placement")
    if placement is not None and not isinstance(placement, dict):
        raise SchemaError(f"{field_prefix}.placement must be a table")

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
        placement=placement,
        cache=_coerce_tristate_bool(entry.get("cache"), f"{field_prefix}.cache"),
        attrs={k: str(v) for k, v in entry.items()},
        source=f"{source}:sections[{index}]",
        original_kind=None,
    )

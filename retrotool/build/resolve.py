"""DataDef → pipeline Section synthesis + inline merge + ordering.

The user-facing rule: a table is defined in exactly one place. Either
entirely inside `tables/<name>.toml` (DataDef with `[section]` sub-table),
or entirely inline in `project.toml` (`[[rom.build.sections]]`). Never
split across two files.

This module materializes DataDefs-with-`[section]` into `Section` objects,
concatenates them with project.toml's inline sections, and orders the result
by offset. A `[rom.build].order = [...]` list in project.toml overrides the
auto-sort — any names listed there come first in the given order, everything
else follows in offset-sorted order.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Iterable, Optional

from retrotool.build.spec import BuildSpec, Section, SectionKind
from retrotool.build.front_ends.schema import SchemaError
from retrotool.project.datadef import DataDef


def _path(p) -> PurePosixPath:
    return PurePosixPath(str(p).replace("\\", "/"))


def _word_wrap_defaults(defaults: Optional[dict]) -> dict:
    """Normalize `[rom.build.section.word-wrap]` (or `word_wrap`) into a snake-cased dict.

    Accepts either kebab or snake keys from project.toml. Only fields useful as
    *project-level* defaults are extracted — per-table things like `entries`
    only belong on the DataDef itself.
    """
    if not defaults:
        return {}
    raw = defaults.get("word-wrap") or defaults.get("word_wrap") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    if "line-width" in raw:
        out["line_width"] = int(raw["line-width"])
    elif "line_width" in raw:
        out["line_width"] = int(raw["line_width"])
    if "max-lines" in raw:
        out["max_lines"] = int(raw["max-lines"])
    elif "max_lines" in raw:
        out["max_lines"] = int(raw["max_lines"])
    if "newline" in raw:
        out["newline"] = str(raw["newline"])
    for wm_key in ("wrap-mode", "wrap_mode"):
        if wm_key in raw:
            out["wrap_mode"] = str(raw[wm_key])
            break
    for fc_key in ("fill-char", "fill_char"):
        if fc_key in raw:
            out["fill_char"] = str(raw[fc_key])
            break
    return out


def _word_wrap_from_datadef(dd: DataDef) -> Optional[dict]:
    raw = dd.extras.get("word_wrap")
    if isinstance(raw, dict):
        ww: dict = {}
        if "line_width" in raw:
            ww["line_width"] = int(raw["line_width"])
        if "max_lines" in raw:
            ww["max_lines"] = int(raw["max_lines"])
        if "entries" in raw:
            ww["entries"] = raw["entries"]
        if "newline" in raw:
            ww["newline"] = str(raw["newline"])
        if "wrap_mode" in raw:
            ww["wrap_mode"] = str(raw["wrap_mode"])
        if "fill_char" in raw:
            ww["fill_char"] = str(raw["fill_char"])
        return ww or None
    return None


def _section_from_datadef(
    dd: DataDef,
    *,
    defaults: Optional[dict] = None,
    en_data_dir: Optional[str] = None,
) -> Section:
    """Build a pipeline Section from a DataDef that has a `[section]` sub-table.

    `defaults` is the project-level `[rom.build.section]` table — its
    `overflow` / `placement` subkeys are used when the datadef's own
    `[section]` doesn't declare them (full-key override, no deep merge).
    `en_data_dir` provides the auto-default root for `file=` synthesis.
    """
    if dd.section is None:
        raise ValueError(f"datadef {dd.name} has no [section] — not a build participant")
    try:
        kind = SectionKind(dd.section.kind)
    except ValueError as e:
        raise SchemaError(
            f"datadef {dd.name}: unknown [section].kind={dd.section.kind!r}"
        ) from e

    defaults = defaults or {}

    # File path: explicit en_file/file on the datadef, else synthesized from
    # `{en_data_dir}/{datadef.name}.txt`. Missing defaults + missing file is
    # an error for file-requiring kinds — raised by the handler at build time,
    # not here (some kinds are file-less).
    if dd.section.file:
        file_path = dd.section.file
    elif en_data_dir:
        file_path = f"{en_data_dir}/{dd.name}.txt"
    else:
        file_path = None

    # Merge overflow: datadef wins if it declared any overflow keys, else
    # fall back to project-level default.
    overflow = dd.section.overflow or defaults.get("overflow") or None

    sec = Section(
        kind=kind,
        from_datadef=dd.name,
        files=[_path(file_path)] if file_path else [],
        grow=dd.section.grow,
        codec=dd.section.codec,
        condition=dd.section.condition,
        cache=dd.section.cache,
        overflow=dict(overflow) if overflow else None,
        source=f"datadef:{dd.name}",
    )
    # Placement: full-key override — datadef > project default.
    placement = dd.section.placement or defaults.get("placement") or None
    if placement:
        sec.placement = dict(placement)
    if dd.pointers is not None:
        sec.pointer_table = dd.pointers.offset
        sec.pointer_size = dd.pointers.size
        sec.count = dd.pointers.count
    if dd.encoding is not None:
        sec.table = _path(dd.encoding.table_file)
        if dd.encoding.fallback is not None:
            sec.fallback_table = _path(dd.encoding.fallback)
        sec.terminator = dd.encoding.terminator
    if dd.data is not None and dd.data.end is not None:
        sec.data_end = dd.data.end
    # Section offset = whichever anchor the DataDef has. For fixed-records
    # that's data.offset (write target). For pointer-typed scripts the
    # natural anchor is the pointer table itself, so ordering works cleanly.
    if sec.offset is None:
        sec.offset = dd.anchor_offset
    if "entries" in dd.extras and sec.count is None:
        sec.count = int(dd.extras["entries"])
    if "block_len" in dd.extras:
        sec.stride = int(dd.extras["block_len"])
    if "textbuf_limit" in dd.extras:
        sec.textbuf_limit = int(dd.extras["textbuf_limit"])
    # Fixed-records field schema: list of {label, start, len, fill}. Flows
    # to handle_fixed_records for text→record packing.
    raw_fields = dd.extras.get("fields")
    if isinstance(raw_fields, list) and raw_fields:
        sec.fields = [dict(f) for f in raw_fields if isinstance(f, dict)]
    ww = _word_wrap_from_datadef(dd)
    ww_defaults = _word_wrap_defaults(defaults)
    if ww is not None or ww_defaults:
        merged_ww = dict(ww_defaults)
        if ww:
            merged_ww.update(ww)
        # Only expose word_wrap if it has the required dims; a stray
        # project-level `newline` with no per-table line_width is a no-op.
        if "line_width" in merged_ww and "max_lines" in merged_ww:
            sec.word_wrap = merged_ww
    # Windowed-script per-entry clobber override.
    if dd.section and dd.section.clobber_lead_entries:
        sec.clobber_lead_entries = [int(x) for x in dd.section.clobber_lead_entries]
    return sec


def sections_from_datadefs(
    datadefs: Iterable[DataDef],
    *,
    defaults: Optional[dict] = None,
    en_data_dir: Optional[str] = None,
) -> list[Section]:
    """Return one Section per DataDef that has a `[section]` sub-table.

    DataDefs without `[section]` are documentation/extract-only and are
    skipped here. `defaults` and `en_data_dir` forward to per-datadef
    resolution (see `_section_from_datadef`).
    """
    return [
        _section_from_datadef(dd, defaults=defaults, en_data_dir=en_data_dir)
        for dd in datadefs if dd.section is not None
    ]


def merge_sections(
    inline: list[Section],
    from_datadefs: list[Section],
    *,
    order: Optional[list[str]] = None,
) -> list[Section]:
    """Merge inline + DataDef-derived sections into an ordered pipeline.

    Rules:
    - Each section needs a unique key. DataDef-derived sections use their
      DataDef name. Inline sections use their `name=` attr when present,
      otherwise a synthetic "inline:<index>@<offset>".
    - `order = [...]` (in project.toml `[rom.build]`) places named sections
      first in the listed order; remaining sections follow, sorted by offset.
    - Name collision (two sections with the same name) → SchemaError.
    - Exact offset collision (two sections patching the same offset) →
      SchemaError — someone defined the same thing twice.
    """
    all_secs: list[Section] = []
    name_map: dict[str, Section] = {}

    for sec in from_datadefs:
        if sec.from_datadef in name_map:
            raise SchemaError(
                f"duplicate DataDef name in pipeline: {sec.from_datadef!r}"
            )
        name_map[sec.from_datadef] = sec
        all_secs.append(sec)

    for i, sec in enumerate(inline):
        key = sec.attrs.get("name") or f"inline:{i}@{sec.offset}"
        if key in name_map:
            raise SchemaError(
                f"section name collision: {key!r} declared both inline and via DataDef"
            )
        name_map[key] = sec
        all_secs.append(sec)

    # Exact offset collision check (None offsets excluded — some kinds are
    # offset-less or inherit from handler).
    seen_offsets: dict[int, str] = {}
    for key, sec in name_map.items():
        if sec.offset is None:
            continue
        if sec.offset in seen_offsets:
            raise SchemaError(
                f"two sections patch offset {hex(sec.offset)}: "
                f"{seen_offsets[sec.offset]!r} and {key!r}"
            )
        seen_offsets[sec.offset] = key

    if order:
        unknown = [n for n in order if n not in name_map]
        if unknown:
            raise SchemaError(
                f"[rom.build].order references unknown sections: {unknown!r} "
                f"(known: {sorted(name_map)!r})"
            )
        ordered = [name_map[n] for n in order]
        remaining = [s for k, s in name_map.items() if k not in set(order)]
        remaining.sort(key=lambda s: (s.offset if s.offset is not None else float("inf")))
        return ordered + remaining

    return sorted(all_secs, key=lambda s: (s.offset if s.offset is not None else float("inf")))


def apply_datadefs_to_spec(
    spec: BuildSpec,
    datadefs: Iterable[DataDef],
    *,
    order: Optional[list[str]] = None,
) -> BuildSpec:
    """Mutate spec in place: replace `spec.sections` with the merged+ordered
    pipeline of inline sections and DataDef-derived sections.

    Project-level defaults (`spec.section_defaults`, `spec.en_data_dir`) are
    forwarded to DataDef resolution so every datadef inherits them unless it
    overrides explicitly.
    """
    dd_list = list(datadefs)
    from_dd = sections_from_datadefs(
        dd_list,
        defaults=spec.section_defaults or None,
        en_data_dir=spec.en_data_dir,
    )
    merged = merge_sections(list(spec.sections), from_dd, order=order)
    spec.sections = merged
    return spec

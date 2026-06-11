"""project_admin — read / update / validate a project's configuration.

The bridge-side service behind the script editor's Project panel
(`retrotool edit` → 🛠 Project), kept UI-agnostic so it is unit-testable
and reusable by future tooling (the project-manager shell, CLI verbs).

Three operations:

* :func:`read_project` — one JSON-friendly dict merging everything the
  panel shows: `[rom]`, top-level language scalars, `[rom.build]` basics,
  the `[editor]` tables, and the resolved section list (inline +
  DataDef-derived, with the DataDef's source file so the UI can say where
  a section is defined).
* :func:`update_project` — apply a flat ``{dotted.key: value}`` change set
  to ``project.toml`` via **tomlkit**, so comments, ordering, and
  formatting survive. A timestamped ``project.toml.bak`` sibling is
  written first; the new text is parse-validated BEFORE it replaces the
  original (atomic temp+rename), so a bad write can never destroy a
  working config.
* :func:`validate_project` — run the real parsers (build front-end +
  editor config) and return their complaints as a list of strings. The
  panel shows exactly what ``retrotool build`` would say — no second
  validation dialect.

Change-set keys are dotted TOML paths, e.g. ``rom.name``,
``build_lang``, ``rom.build.output_dir``, ``editor.cols_per_line``,
``editor.control_codes.newline``, ``editor.preview.font``. Values are
plain scalars (str/int/bool); ``None`` deletes the key. Intermediate
tables are created as needed.
"""
from __future__ import annotations

import shutil
import tempfile
import time
import tomllib
import os
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "ProjectAdminError",
    "create_datadef",
    "diff_datadef",
    "diff_preview",
    "read_datadef",
    "read_project",
    "scan_sections",
    "update_datadef",
    "update_project",
    "validate_project",
]


class ProjectAdminError(RuntimeError):
    """A project.toml read/update could not be performed safely."""


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

def _section_summary(sec) -> dict:
    """JSON-friendly summary of one build Section for the panel's list."""
    return {
        "name": (sec.from_datadef or sec.attrs.get("name")
                 or sec.attrs.get("alias") or ""),
        "kind": sec.kind.value,
        "file": str(sec.files[0]) if sec.files else "",
        "table": str(sec.table) if sec.table else "",
        "count": sec.count,
        "pointer_table": sec.pointer_table,
        "pointer_size": sec.pointer_size,
        "placement": (sec.placement or {}).get("mode", ""),
        "from_datadef": sec.from_datadef or "",
        "source": sec.source or "",
    }


def read_project(root: Path | str) -> dict:
    """Merged, JSON-friendly view of the project for the Project panel.

    Never raises on a half-broken project — parse problems land in the
    returned ``problems`` list so the panel can still open and show what
    it can (you need the panel most when the config is broken).
    """
    root = Path(root).resolve()
    pt = root / "project.toml"
    out: dict = {
        "root": str(root),
        "exists": pt.exists(),
        "rom": {},
        "languages": {},          # lang -> data dir
        "build_lang": "",
        "data_dirs": [],
        "build": {},
        "editor": {},
        "sections": [],
        "problems": [],
    }
    if not pt.exists():
        out["problems"].append(f"no project.toml in {root}")
        return out

    try:
        data = tomllib.loads(pt.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        out["problems"].append(f"project.toml: {e}")
        return out

    rom = data.get("rom") or {}
    out["rom"] = {
        "name": rom.get("name", ""),
        "file": rom.get("file", ""),
        "mapping": rom.get("mapping", ""),
        "size": str(rom.get("size", "")),
    }
    out["languages"] = {
        k[: -len("_data_dir")]: v for k, v in data.items()
        if isinstance(k, str) and k.endswith("_data_dir") and isinstance(v, str)
    }
    out["build_lang"] = data.get("build_lang", "")
    out["data_dirs"] = list(data.get("data_dirs") or [])
    build = rom.get("build") or {}
    out["build"] = {
        "output_dir": (build.get("output_dir") or build.get("out-dir")
                       or build.get("out_dir") or ""),
        "jobs": build.get("jobs"),
        "diff": build.get("diff", ""),
        "placement_mode": ((build.get("section") or {}).get("placement")
                           or {}).get("mode", ""),
    }
    out["editor"] = data.get("editor") or {}

    # Resolved section list via the real loader (includes DataDef-derived
    # sections). Failures are problems, not exceptions.
    try:
        from retrotool.build.project import load_spec
        spec, _ = load_spec(pt)
        out["sections"] = [_section_summary(s) for s in spec.sections]
    except Exception as e:  # noqa: BLE001 — show, don't crash the panel
        out["problems"].append(f"section list unavailable: {e}")

    out["problems"].extend(validate_project(root))
    return out


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

def _set_dotted(doc, dotted: str, value: Any) -> None:
    """Set/delete `dotted` (e.g. "editor.control_codes.newline") in a
    tomlkit document. None deletes; intermediate tables are created."""
    import tomlkit

    parts = dotted.split(".")
    node = doc
    for p in parts[:-1]:
        if p not in node or not hasattr(node[p], "__setitem__"):
            if value is None:
                return          # deleting under a missing table: no-op
            node[p] = tomlkit.table()
        node = node[p]
    leaf = parts[-1]
    if value is None:
        if leaf in node:
            del node[leaf]
        return
    node[leaf] = value


def _edited_text(path: Path, changes: dict[str, Any]) -> tuple[str, str]:
    """(original, edited) text of a toml file after applying `changes`.
    Pure — nothing is written."""
    import tomlkit

    if not path.exists():
        raise ProjectAdminError(f"no {path.name} in {path.parent}")
    original = path.read_text(encoding="utf-8")
    try:
        doc = tomlkit.parse(original)
    except Exception as e:
        raise ProjectAdminError(f"{path.name} does not parse: {e}") from e
    for key, value in changes.items():
        if not key or not all(part.strip() for part in key.split(".")):
            raise ProjectAdminError(f"bad change key: {key!r}")
        _set_dotted(doc, key, value)
    return original, tomlkit.dumps(doc)


def _write_validated(path: Path, new_text: str) -> Path:
    """Write `new_text` to `path` the safe way: re-parse first (refuse to
    write a file that wouldn't load), timestamped .bak of the original,
    atomic temp+rename. Returns the backup path."""
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as e:
        raise ProjectAdminError(
            f"refusing to write: edited {path.name} would not parse: {e}"
        ) from e
    bak = path.with_name(
        f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, bak)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return bak


def update_toml_file(path: Path, changes: dict[str, Any]) -> Path:
    """Apply a ``{dotted.key: value}`` change set to any toml file —
    comment/format-preserving, .bak'd, parse-validated before replace.
    Returns the backup path."""
    _, new_text = _edited_text(path, changes)
    return _write_validated(path, new_text)


def update_project(root: Path | str, changes: dict[str, Any]) -> Path:
    """Apply a ``{dotted.key: value}`` change set to project.toml.

    Comment/format-preserving (tomlkit). Safety order: parse current →
    apply changes in memory → **re-parse the new text** (tomllib) → write
    timestamped ``.bak`` of the original → atomic temp+rename. Returns
    the backup path. Raises :class:`ProjectAdminError` if the project
    file is missing/unparseable or the resulting text doesn't parse
    (nothing is written in that case).
    """
    root = Path(root).resolve()
    return update_toml_file(root / "project.toml", changes)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def validate_project(root: Path | str) -> list[str]:
    """Run the REAL parsers and return their complaints (empty = clean).

    Mirrors what `retrotool build` / `retrotool edit` would reject, so
    the panel never invents its own validation dialect.
    """
    root = Path(root).resolve()
    pt = root / "project.toml"
    problems: list[str] = []
    if not pt.exists():
        return [f"no project.toml in {root}"]

    try:
        from retrotool.build.project import load_spec
        load_spec(pt)
    except Exception as e:  # noqa: BLE001 — parser errors are the payload
        problems.append(f"build: {e}")

    try:
        from retrotool.script.editor_config import load_editor_config
        cfg = load_editor_config(root)
        if not cfg.data_dir.is_dir():
            problems.append(f"editor: script folder not found: {cfg.data_dir}")
        if cfg.table is None:
            problems.append(
                "editor: no .tbl table found — byte counts will be approximate "
                "(set [editor].table or add tables/*_<lang>.tbl)")
    except Exception as e:  # noqa: BLE001
        problems.append(f"editor: {e}")

    return problems


def diff_toml_file(path: Path, changes: dict[str, Any]) -> Optional[str]:
    """Unified diff of what `update_toml_file(changes)` WOULD write — shown
    before the user confirms. None when nothing changes."""
    import difflib

    original, new_text = _edited_text(path, changes)
    if new_text == original:
        return None
    return "".join(difflib.unified_diff(
        original.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=path.name, tofile=f"{path.name} (edited)",
    ))


def diff_preview(root: Path | str, changes: dict[str, Any]) -> Optional[str]:
    """Unified diff for a project.toml change set (see diff_toml_file)."""
    root = Path(root).resolve()
    return diff_toml_file(root / "project.toml", changes)


# ---------------------------------------------------------------------------
# DataDef files (section wizard)
# ---------------------------------------------------------------------------

def _data_dirs(root: Path) -> list[str]:
    pt = root / "project.toml"
    if not pt.exists():
        return []
    try:
        data = tomllib.loads(pt.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return []
    return [d for d in (data.get("data_dirs") or []) if isinstance(d, str)]


def datadef_path(root: Path | str, name: str) -> Optional[Path]:
    """Find the DataDef toml whose ``[table].name`` is `name`, searching the
    project's ``data_dirs``. None when not found."""
    root = Path(root).resolve()
    for d in _data_dirs(root):
        for tml in sorted((root / d).glob("*.toml")):
            try:
                doc = tomllib.loads(tml.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError:
                continue
            if (doc.get("table") or {}).get("name") == name:
                return tml
    return None


def read_datadef(root: Path | str, name: str) -> dict:
    """Raw parsed view of one DataDef toml for the section editor:
    ``{path, table, encoding, pointers, data, section, problems}``."""
    root = Path(root).resolve()
    path = datadef_path(root, name)
    if path is None:
        return {"path": "", "problems": [f"no DataDef named {name!r} found "
                                         f"under data_dirs {_data_dirs(root)}"]}
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        return {"path": str(path.relative_to(root)), "problems": [str(e)]}
    return {
        "path": str(path.relative_to(root)),
        "table": doc.get("table") or {},
        "encoding": doc.get("encoding") or {},
        "pointers": doc.get("pointers") or {},
        "data": doc.get("data") or {},
        "section": doc.get("section") or {},
        "problems": [],
    }


def update_datadef(root: Path | str, name: str, changes: dict[str, Any]) -> Path:
    """Comment-preserving change-set write to the DataDef named `name`
    (same dotted-key / .bak / validate-before-replace flow as
    update_project). Returns the backup path."""
    root = Path(root).resolve()
    path = datadef_path(root, name)
    if path is None:
        raise ProjectAdminError(f"no DataDef named {name!r}")
    return update_toml_file(path, changes)


def diff_datadef(root: Path | str, name: str,
                 changes: dict[str, Any]) -> Optional[str]:
    """Unified diff for a DataDef change set, written nowhere."""
    root = Path(root).resolve()
    path = datadef_path(root, name)
    if path is None:
        raise ProjectAdminError(f"no DataDef named {name!r}")
    return diff_toml_file(path, changes)


def _ensure_data_dirs(root: Path) -> str:
    """Make sure project.toml declares a ``data_dirs`` and the directory
    exists; returns the (first) defs dir, creating ``defs/`` if needed.

    TOML requires top-level scalars BEFORE the first table header, so when
    the key is missing it is inserted textually (after leading comments),
    not appended via tomlkit (which would land it after [rom...])."""
    dirs = _data_dirs(root)
    if dirs:
        (root / dirs[0]).mkdir(parents=True, exist_ok=True)
        return dirs[0]
    pt = root / "project.toml"
    original = pt.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    insert_at = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#"):
            insert_at = i + 1
            continue
        break
    lines.insert(insert_at,
                 '# Folders scanned for DataDef tomls (added by the section '
                 'wizard).\ndata_dirs = ["defs"]\n')
    _write_validated(pt, "".join(lines))
    (root / "defs").mkdir(parents=True, exist_ok=True)
    return "defs"


def _hex6(v: Optional[int]) -> str:
    return f"${int(v):06X}"


def create_datadef(root: Path | str, spec: dict[str, Any]) -> Path:
    """Create a new DataDef toml from the section wizard's spec and return
    its path. Refuses to overwrite an existing file or reuse an existing
    DataDef name.

    spec keys (name required, rest optional):
      name, kind ("script"), table_file, terminator (int),
      ptr_offset / ptr_count / ptr_size (int, PC offset / entries / 2|3),
      data_offset / data_end (int PC offsets), placement_mode ("relocate").
    """
    root = Path(root).resolve()
    name = (spec.get("name") or "").strip()
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        raise ProjectAdminError(
            f"bad section name {name!r} (letters/digits/_/- only)")
    if datadef_path(root, name) is not None:
        raise ProjectAdminError(f"a DataDef named {name!r} already exists")

    defs_dir = _ensure_data_dirs(root)
    path = root / defs_dir / f"{name}.toml"
    if path.exists():
        raise ProjectAdminError(f"{path.relative_to(root)} already exists")

    kind = (spec.get("kind") or "script").strip()
    placement = (spec.get("placement_mode") or "relocate").strip()
    lines = [
        f"# {name}.toml — DataDef created by the retrotool section wizard.",
        "# Every key is documented in docs/project-toml-reference.md.",
        "",
        "[table]",
        f'name = "{name}"',
        'type = "pointer"',
        "",
    ]
    if spec.get("table_file"):
        lines += ["[encoding]",
                  f'table_file = "{spec["table_file"]}"']
        if spec.get("terminator") is not None:
            lines += ["# End-of-string byte the extractor walks to / the "
                      "encoder appends.",
                      f"terminator = 0x{int(spec['terminator']):02X}"]
        lines += [""]
    if spec.get("ptr_offset") is not None:
        lines += [
            "[pointers]",
            "# PC file offset of the pointer table.",
            f"offset = \"{_hex6(spec['ptr_offset'])}\"",
            f"count = {int(spec.get('ptr_count') or 0)}",
            f"size = {int(spec.get('ptr_size') or 2)}",
            "",
        ]
    if spec.get("data_offset") is not None:
        end = spec.get("data_end")
        lines += [
            "[data]",
            "# Original strings region (PC file offsets); informational "
            "for relocate mode.",
            f"offset = \"{_hex6(spec['data_offset'])}\"",
        ]
        if end is not None:
            lines += [f"end = \"{_hex6(end)}\""]
        lines += [""]
    lines += [
        "[section]",
        f'kind = "{kind}"',
        "[section.placement]",
        f'mode = "{placement}"',
        "",
    ]
    text = "\n".join(lines)
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:  # template bug — never user's fault
        raise ProjectAdminError(f"generated DataDef does not parse: {e}") from e
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Pointer-table scan (wizard seeding)
# ---------------------------------------------------------------------------

def scan_sections(root: Path | str, entry_size: int = 2,
                  min_entries: int = 8,
                  max_candidates: int = 40) -> dict:
    """Scan the project's source ROM for candidate pointer tables to seed
    the section wizard. Returns ``{candidates: [...], error: str|None}``;
    candidates are sorted best-first (count × monotonic fraction) and
    capped at `max_candidates`."""
    root = Path(root).resolve()
    pt = root / "project.toml"
    try:
        data = tomllib.loads(pt.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        return {"candidates": [], "error": f"project.toml: {e}"}
    rom_rel = (data.get("rom") or {}).get("file")
    if not rom_rel:
        return {"candidates": [], "error": "[rom].file not set"}
    rom_path = root / rom_rel
    if not rom_path.exists():
        return {"candidates": [],
                "error": f"source ROM not found: {rom_rel}"}

    from retrotool.core.rom import _strip_smc_header
    from retrotool.heuristics.pointers import scan_pointer_tables
    from retrotool.project.schema import mapping_to_address_type
    _, body = _strip_smc_header(rom_path.read_bytes())
    mapping = (data.get("rom") or {}).get("mapping") or "lorom"
    try:
        addr_type = mapping_to_address_type(mapping)
    except Exception:
        addr_type = 1  # LOROM1

    found = scan_pointer_tables(
        bytes(body), entry_size=int(entry_size),
        address_type=addr_type, min_entries=int(min_entries),
    )
    found.sort(key=lambda c: c.count * c.monotonic_fraction, reverse=True)
    out = [{
        "offset": c.offset,
        "offset_hex": _hex6(c.offset),
        "entry_size": c.entry_size,
        "count": c.count,
        "target_low": c.target_low,
        "target_low_hex": _hex6(c.target_low),
        "target_high": c.target_high,
        "target_high_hex": _hex6(c.target_high),
        "monotonic": round(c.monotonic_fraction, 3),
    } for c in found[:int(max_candidates)]]
    return {"candidates": out, "error": None,
            "truncated": len(found) > len(out)}

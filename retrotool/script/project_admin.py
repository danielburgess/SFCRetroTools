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
    "diff_preview",
    "read_project",
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


def update_project(root: Path | str, changes: dict[str, Any]) -> Path:
    """Apply a ``{dotted.key: value}`` change set to project.toml.

    Comment/format-preserving (tomlkit). Safety order: parse current →
    apply changes in memory → **re-parse the new text** (tomllib) → write
    timestamped ``.bak`` of the original → atomic temp+rename. Returns
    the backup path. Raises :class:`ProjectAdminError` if the project
    file is missing/unparseable or the resulting text doesn't parse
    (nothing is written in that case).
    """
    import tomlkit

    root = Path(root).resolve()
    pt = root / "project.toml"
    if not pt.exists():
        raise ProjectAdminError(f"no project.toml in {root}")
    original = pt.read_text(encoding="utf-8")
    try:
        doc = tomlkit.parse(original)
    except Exception as e:
        raise ProjectAdminError(f"project.toml does not parse: {e}") from e

    for key, value in changes.items():
        if not key or not all(part.strip() for part in key.split(".")):
            raise ProjectAdminError(f"bad change key: {key!r}")
        _set_dotted(doc, key, value)

    new_text = tomlkit.dumps(doc)
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as e:
        raise ProjectAdminError(
            f"refusing to write: edited project.toml would not parse: {e}"
        ) from e

    bak = pt.with_name(f"project.toml.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(pt, bak)
    fd, tmp = tempfile.mkstemp(dir=str(pt.parent), prefix=".project.toml.",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_text)
        os.replace(tmp, pt)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return bak


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


def diff_preview(root: Path | str, changes: dict[str, Any]) -> Optional[str]:
    """Unified diff of what `update_project(changes)` WOULD write — the
    panel shows this before the user confirms. None when nothing changes."""
    import difflib
    import tomlkit

    root = Path(root).resolve()
    pt = root / "project.toml"
    if not pt.exists():
        raise ProjectAdminError(f"no project.toml in {root}")
    original = pt.read_text(encoding="utf-8")
    doc = tomlkit.parse(original)
    for key, value in changes.items():
        _set_dotted(doc, key, value)
    new_text = tomlkit.dumps(doc)
    if new_text == original:
        return None
    return "".join(difflib.unified_diff(
        original.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile="project.toml", tofile="project.toml (edited)",
    ))

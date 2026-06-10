"""language — stage a retrotool project for a NEW translation language.

Adopted from the rbshura project's ``tools/setup_language.py`` (which was
written to be generic across retrotool projects); now shipped as
``retrotool lang new``. Works on any project laid out the standard way: a
``project.toml`` with ``<lang>_data_dir`` script roots, optional
``data_dirs`` DataDef folders, and ``[[rom.build.sections]]`` asset entries.

Given a SOURCE language (default: en) and a NEW language code, the plan:

  1. Copies the source script folder  ``data/<src>`` -> ``data/<new>``
     (the new language starts as a copy of the source script and is
     translated in place, e.g. with ``retrotool edit``).
  2. project.toml:
       * adds   ``<new>_data_dir = "data/<new>"``
       * sets   ``build_lang = "<new>"``  (retrotool >= 0.9.3 reads the
         script from the matching ``<lang>_data_dir``)
       * renames ``[rom] name`` (suffix ``_<src>`` -> ``_<new>``, else
         appends) so the built ROM lands under a per-language name.
  3. Forks every LANGUAGE-BEARING asset the tomls point at, next to the
     original, renamed with the language code, and repoints the tomls:
       * ``[[rom.build.sections]]`` with kind "bin" / "graphics"
         (fonts, art)
       * ``[encoding] table_file`` in every DataDef toml under
         ``data_dirs``
     Naming: a stem already suffixed with a declared language code is
     re-suffixed (``rbshura_en.tbl`` -> ``rbshura_fr.tbl``); anything else
     gets ``_<new>`` appended (``logo.png`` -> ``logo_fr.png``). The copied
     CONTENT comes from the ``src_lang`` variant when one exists on disk.
     NOT forked (shared between languages): kind="asar"/"python" engine
     patches and the ``[rom] file`` source ROM — pass ``fork_all=True`` to
     fork those too.

toml files are edited TEXTUALLY (exact quoted-string replacement) so all
comments and formatting are preserved; tomllib is used only to discover
what to replace. Every replacement is verified to still match at apply
time — if a file changed underneath the plan, nothing is written.

Library API: :func:`build_language_plan` -> :class:`LanguagePlan`,
:func:`apply_plan`, :func:`format_plan`. All validation errors raise
:class:`LanguageSetupError` (the CLI turns them into exit-code-2 messages).
"""
from __future__ import annotations

import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Edit",
    "LanguagePlan",
    "LanguageSetupError",
    "apply_plan",
    "build_language_plan",
    "declared_languages",
    "format_plan",
    "split_lang_suffix",
]

ASSET_KINDS = {"bin", "graphics"}       # section kinds whose `file` is forked
SHARED_KINDS = {"asar", "python"}       # engine code — shared, never forked

LANG_CODE_RE = re.compile(r"[a-z0-9]+(_[a-z0-9]+)*")


class LanguageSetupError(ValueError):
    """A problem with the project layout or the requested languages."""


@dataclass
class Edit:
    """One exact-string replacement inside a text file."""
    path: Path
    old: str
    new: str
    why: str


@dataclass
class LanguagePlan:
    """Everything ``retrotool lang new`` intends to do, before doing it."""
    copies: list[tuple[Path, Path, str]] = field(default_factory=list)  # (src, dst, why)
    edits: list[Edit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def copy(self, src: Path, dst: Path, why: str) -> None:
        self.copies.append((src, dst, why))

    def edit(self, path: Path, old: str, new: str, why: str) -> None:
        self.edits.append(Edit(path, old, new, why))

    @property
    def empty(self) -> bool:
        return not self.copies and not self.edits


def declared_languages(cfg: dict) -> list[str]:
    """Language codes declared via ``<lang>_data_dir`` scalars, sorted."""
    return sorted(k[: -len("_data_dir")] for k in cfg
                  if isinstance(k, str) and k.endswith("_data_dir"))


def split_lang_suffix(stem: str, known_langs: set[str]) -> tuple[str, str | None]:
    """``'rbshura_en' -> ('rbshura', 'en')``; ``'ascii' -> ('ascii', None)``.
    Longest language code wins (``br_pt`` before ``pt``)."""
    for lang in sorted(known_langs, key=len, reverse=True):
        if stem.endswith(f"_{lang}"):
            return stem[: -len(lang) - 1], lang
    return stem, None


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------

def build_language_plan(
    root: Path,
    src_lang: str,
    new_lang: str,
    fork_all: bool = False,
) -> LanguagePlan:
    """Construct the staging plan for ``new_lang`` seeded from ``src_lang``.

    Raises :class:`LanguageSetupError` on a bad language code, identical
    src/new, missing source folder, or a project.toml the textual editor
    can't anchor in. Re-running for an already-declared language is allowed
    and yields a repair plan (only the missing copies / repoints).
    """
    if not LANG_CODE_RE.fullmatch(new_lang or ""):
        raise LanguageSetupError(
            "language code must be lowercase letters/digits/underscores, "
            "e.g. fr / br_pt")
    if new_lang == src_lang:
        raise LanguageSetupError(
            "the new language must differ from the source language")

    pt_path = root / "project.toml"
    if not pt_path.exists():
        raise LanguageSetupError(f"no project.toml in {root}")
    pt_text = pt_path.read_text(encoding="utf-8")
    cfg = tomllib.loads(pt_text)
    plan = LanguagePlan()

    # --- 1. script folder copy ------------------------------------------
    src_key = f"{src_lang}_data_dir"
    if src_key not in cfg:
        langs = ", ".join(declared_languages(cfg)) or "none"
        raise LanguageSetupError(
            f"project.toml has no `{src_key}` — available languages: {langs}")
    src_dir = root / cfg[src_key]
    if not src_dir.is_dir():
        raise LanguageSetupError(
            f"source script folder {src_dir} does not exist")
    # New folder is a sibling of the source folder (data/en -> data/fr).
    new_dir = src_dir.parent / new_lang
    new_key = f"{new_lang}_data_dir"
    if new_key in cfg:
        plan.notes.append(f"{new_key} already declared in project.toml — keeping it")
        new_dir = root / cfg[new_key]
    if new_dir.exists():
        plan.notes.append(
            f"{new_dir.relative_to(root)} already exists — script copy skipped")
    else:
        plan.copy(src_dir, new_dir, "script folder (translate these files)")

    # --- 2. project.toml language keys -----------------------------------
    new_dir_rel = new_dir.relative_to(root).as_posix()
    src_line_re = re.compile(rf"^({re.escape(src_key)}\s*=\s*.*)$", re.M)
    m = src_line_re.search(pt_text)
    if not m:
        raise LanguageSetupError(
            f"could not find the `{src_key} = ...` line in project.toml")
    if new_key not in cfg:
        plan.edit(pt_path, m.group(1),
                  m.group(1) + f'\n{new_key} = "{new_dir_rel}"',
                  f"declare {new_key}")
    bl = re.search(r"^(build_lang\s*=\s*\"[^\"]*\")", pt_text, re.M)
    if bl:
        if bl.group(1) != f'build_lang = "{new_lang}"':
            plan.edit(pt_path, bl.group(1), f'build_lang = "{new_lang}"',
                      f"build the {new_lang} ROM")
    else:
        anchor = (m.group(1) + f'\n{new_key} = "{new_dir_rel}"'
                  if new_key not in cfg else m.group(1))
        plan.edit(pt_path, anchor, anchor + f'\nbuild_lang = "{new_lang}"',
                  f"build the {new_lang} ROM")

    # Language codes we can recognize as a `_<lang>` suffix (on the [rom]
    # name and on asset filenames): every declared `<lang>_data_dir` plus
    # the languages involved in this run.
    known_langs = set(declared_languages(cfg))
    known_langs.update((src_lang, new_lang))

    # --- 2b. [rom] name ---------------------------------------------------
    rom = cfg.get("rom", {})
    old_name = rom.get("name")
    if old_name:
        base, cur = split_lang_suffix(old_name, known_langs)
        # Re-suffix any recognized language code (so a repair re-run on a
        # name already ending in `_<new>` is a no-op, not `_fr_fr`).
        new_name = f"{base}_{new_lang}" if cur else f"{old_name}_{new_lang}"
        if old_name != new_name:
            plan.edit(pt_path, f'name = "{old_name}"', f'name = "{new_name}"',
                      f"built ROM -> {new_name}.sfc")

    # --- 3. fork toml-pointed assets --------------------------------------
    rom_file = rom.get("file")

    planned_copies: set[str] = set()

    def fork_ref(toml_path: Path, ref: str, why: str) -> None:
        """Plan: copy `ref` (project-relative path) to its language-forked
        name and repoint every textual occurrence in `toml_path`. The copy
        is planned once even when many tomls reference the same asset.

        The CONTENT comes from the `src_lang` variant of the asset when one
        exists (so staging `de` from `en` on a checkout currently pointed
        at `_fr` assets copies the `_en` files, not the French ones);
        otherwise from whatever the toml currently points at."""
        asset = root / ref
        base, cur_lang = split_lang_suffix(asset.stem, known_langs)
        if cur_lang == new_lang:
            # Already forked (re-run repair) — nothing to copy or repoint.
            return
        forked = asset.with_name(f"{base}_{new_lang}{asset.suffix}")
        forked_ref = forked.relative_to(root).as_posix()
        src_variant = asset.with_name(f"{base}_{src_lang}{asset.suffix}")
        copy_src = (src_variant
                    if cur_lang != src_lang and src_variant.exists() else asset)
        if not copy_src.exists():
            plan.notes.append(
                f"SKIP {ref} (referenced by {toml_path.name} but missing on disk)")
            return
        if ref not in planned_copies:
            planned_copies.add(ref)
            if forked.exists():
                plan.notes.append(
                    f"{forked_ref} already exists — copy skipped, still repointed")
            else:
                plan.copy(copy_src, forked, why)
        plan.edit(toml_path, f'"{ref}"', f'"{forked_ref}"', f"repoint {why}")

    seen: set[str] = set()
    for sec in rom.get("build", {}).get("sections", []):
        kind, ref = sec.get("kind"), sec.get("file")
        if not ref or ref in seen or ref == rom_file:
            continue
        if kind in ASSET_KINDS or (fork_all and kind in SHARED_KINDS):
            seen.add(ref)
            fork_ref(pt_path, ref, f"{kind} asset")
        elif kind in SHARED_KINDS:
            plan.notes.append(
                f"shared {kind} patch kept as-is: {ref} (--fork-all to fork)")

    # DataDef tomls under data_dirs: fork each encoding table_file.
    for d in cfg.get("data_dirs", []):
        for tml in sorted((root / d).glob("*.toml")):
            try:
                sub = tomllib.loads(tml.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as e:
                plan.notes.append(
                    f"SKIP {tml.relative_to(root)} (toml parse error: {e})")
                continue
            ref = sub.get("encoding", {}).get("table_file")
            if ref:
                fork_ref(tml, ref, "encoding table")

    return plan


# ---------------------------------------------------------------------------
# Plan execution / display
# ---------------------------------------------------------------------------

def apply_plan(plan: LanguagePlan, dry_run: bool = False) -> None:
    """Execute the plan. Edits are applied per-file against the live text so
    chained anchors (data_dir line -> build_lang insert) compose; if any
    expected string is gone the whole apply aborts with
    :class:`LanguageSetupError` and nothing further is written."""
    for src, dst, _ in plan.copies:
        if dry_run:
            continue
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    by_file: dict[Path, list[Edit]] = {}
    for e in plan.edits:
        by_file.setdefault(e.path, []).append(e)
    for path, edits in by_file.items():
        text = path.read_text(encoding="utf-8")
        for e in edits:
            if e.old not in text:
                raise LanguageSetupError(
                    f"expected `{e.old}` in {path} but it is gone — file "
                    f"changed underneath the plan; nothing else written.")
            text = text.replace(e.old, e.new)
        if not dry_run:
            path.write_text(text, encoding="utf-8")


def format_plan(root: Path, plan: LanguagePlan) -> str:
    """Human-readable plan listing (what the CLI prints before confirming)."""
    def rel(p: Path) -> str:
        return p.relative_to(root).as_posix()
    lines = ["=== plan ==="]
    for src, dst, why in plan.copies:
        lines.append(f"  COPY  {rel(src)}  ->  {rel(dst)}   [{why}]")
    for e in plan.edits:
        first = e.new.splitlines()[0] if "\n" not in e.old else "(multi-line)"
        lines.append(f"  EDIT  {rel(e.path)}: {e.old.splitlines()[0][:60]}"
                     f"  ->  {first[:60]}   [{e.why}]")
    for n in plan.notes:
        lines.append(f"  NOTE  {n}")
    if plan.empty:
        lines.append("  (nothing to do)")
    return "\n".join(lines)

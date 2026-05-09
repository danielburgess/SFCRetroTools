"""Golden test: every CLI flag declared in retrotool/cli.py is documented
in README.md, and the CLI Reference section is present and complete.

Walks the actual argparse tree built by `_build_parser()` rather than
regex-scraping the source — adding a flag in cli.py that someone forgets to
document will fail this test without anyone needing to update a hardcoded list.

What we verify, in order:

  1. The README contains a `## CLI Reference` section.
  2. Each top-level subcommand (`build`, `extract`, `migrate`, `libsfx`)
     and each libsfx leaf (`scaffold`, `build`, `info`, `clean`) is named
     under its own header.
  3. Every option string (`--foo`, `-X`) on every (sub-)parser appears
     literally somewhere in the README.

The forward direction is what matters: it stops the docs from going stale
whenever the parser grows. We don't enforce the reverse (README → parser)
because the README contains prose that mentions option strings as part of
narrative explanations, and a flag-counting reverse check would be noisy.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from retrotool.cli import _build_parser


README_PATH = Path(__file__).parent.parent / "README.md"


def _all_subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """Recursively collect every (sub-)parser keyed by dotted command path.

    Top-level parser is keyed `""`; `retrotool build` is keyed `"build"`;
    `retrotool libsfx scaffold` is keyed `"libsfx.scaffold"`. Argparse stores
    subparsers on a hidden `_SubParsersAction` whose `.choices` maps name →
    nested parser, which is exactly the recursion structure we want.
    """
    out: dict[str, argparse.ArgumentParser] = {"": parser}

    def _walk(p: argparse.ArgumentParser, prefix: str) -> None:
        for action in p._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    key = f"{prefix}.{name}" if prefix else name
                    out[key] = sub
                    _walk(sub, key)

    _walk(parser, "")
    return out


def _flags(parser: argparse.ArgumentParser) -> set[str]:
    """All option strings on this parser (excludes positional args + the
    auto-generated `-h/--help`, which argparse adds to every parser)."""
    flags: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            continue
        if isinstance(action, argparse._HelpAction):
            continue
        flags.update(action.option_strings)
    return flags


@pytest.fixture(scope="module")
def readme() -> str:
    return README_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parser() -> argparse.ArgumentParser:
    return _build_parser()


@pytest.fixture(scope="module")
def subparsers(parser) -> dict[str, argparse.ArgumentParser]:
    return _all_subparsers(parser)


def test_readme_has_cli_reference_section(readme):
    assert "## CLI Reference" in readme, (
        "README is missing the `## CLI Reference` section. Restore it or "
        "update this test if the section was renamed."
    )


@pytest.mark.parametrize("subcmd", [
    "build", "extract", "migrate",
    "libsfx",
    "libsfx.scaffold", "libsfx.build", "libsfx.info", "libsfx.clean",
])
def test_subcommand_has_dedicated_section(readme, subparsers, subcmd):
    """Each subcommand must have a header that names it. The CLI Reference
    uses `### \\`retrotool build\\`` and `#### \\`retrotool libsfx scaffold <dir>\\``
    style headers — match that prefix, not the literal whole header, so
    additions like `<dir>` don't break the test."""
    assert subcmd in subparsers, f"parser is missing subcommand {subcmd!r}"
    leaf = subcmd.replace(".", " ")
    expected = f"`retrotool {leaf}"
    assert expected in readme, (
        f"README is missing a header for `retrotool {leaf}` "
        f"(looked for {expected!r} in any section title)."
    )


def test_every_parser_flag_appears_in_readme(readme, subparsers):
    """For every (sub-)parser flag, assert the literal option string
    (e.g. `--no-cache`, `-D`) is somewhere in the README. Catches any new
    flag added to cli.py that the author forgot to document."""
    missing: list[tuple[str, str]] = []
    for cmd, sub in subparsers.items():
        for flag in _flags(sub):
            if flag not in readme:
                missing.append((cmd or "<root>", flag))
    if missing:
        rows = "\n".join(f"  {cmd}: {flag}" for cmd, flag in sorted(missing))
        pytest.fail(
            f"{len(missing)} CLI flag(s) declared in cli.py are not "
            f"documented in README.md:\n{rows}\n\n"
            f"Add them to the relevant section under `## CLI Reference`."
        )


def test_short_and_long_aliases_both_documented(readme, subparsers):
    """When a flag has both a short (`-X`) and long (`--xxx`) form, both
    must appear in the README — readers shouldn't have to guess that `-D`
    is short for `--define`."""
    missing: list[tuple[str, str, str]] = []
    for cmd, sub in subparsers.items():
        for action in sub._actions:
            if isinstance(action, (argparse._SubParsersAction, argparse._HelpAction)):
                continue
            opts = action.option_strings
            if len(opts) < 2:
                continue
            for opt in opts:
                if opt not in readme:
                    missing.append((cmd or "<root>", "/".join(opts), opt))
    if missing:
        rows = "\n".join(
            f"  {cmd}: {pair} — {opt} not in README"
            for cmd, pair, opt in sorted(missing)
        )
        pytest.fail(
            f"{len(missing)} flag alias(es) only partially documented:\n{rows}"
        )


# ---- Python-API drift guards ---------------------------------------------
#
# The README markets retrotool as a library-first toolkit; every snippet in a
# `\`\`\`python` fenced block must continue to import and resolve, otherwise
# users land on broken examples. This caught the deletion of `retrotool.Rom`
# in the cleanup commit (the README still documented `Rom.load(...)`).

import ast
import importlib
import re


def _python_blocks(text: str) -> list[tuple[int, str]]:
    """Yield (1-based block index, source) for every fenced ```python block.

    Ordering matches README order so failure messages name the block the
    reader can find by counting from the top.
    """
    return list(enumerate(
        re.findall(r"```python\n(.*?)\n```", text, flags=re.DOTALL),
        start=1,
    ))


def test_readme_python_blocks_all_compile(readme):
    """Each fenced ```python block must parse — guards against typo / drift
    that breaks the tutorial examples even before imports are checked."""
    bad: list[tuple[int, str]] = []
    for idx, block in _python_blocks(readme):
        try:
            compile(block, f"<README block {idx}>", "exec")
        except SyntaxError as e:
            bad.append((idx, str(e)))
    if bad:
        rows = "\n".join(f"  block #{i}: {msg}" for i, msg in bad)
        pytest.fail(f"{len(bad)} README python block(s) failed to parse:\n{rows}")


def test_readme_python_block_imports_resolve(readme):
    """For every `from retrotool[...] import A, B` (and `import retrotool[...]`)
    line in a README python block, the module must import and every named
    symbol must exist on it. This is the canary that fired on the missing
    `Rom` re-export — keep it green and the documented Python API can't
    silently rot away.
    """
    failures: list[tuple[int, str, str]] = []
    for idx, block in _python_blocks(readme):
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue  # already reported by the compile test
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if not mod.startswith("retrotool"):
                    continue  # third-party (Path, etc.) — not our concern
                try:
                    m = importlib.import_module(mod)
                except Exception as e:  # noqa: BLE001
                    failures.append((idx, f"from {mod}", repr(e)))
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    if not hasattr(m, alias.name):
                        failures.append((
                            idx, f"from {mod} import {alias.name}",
                            f"AttributeError: module {mod!r} has no "
                            f"attribute {alias.name!r}",
                        ))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if not alias.name.startswith("retrotool"):
                        continue
                    try:
                        importlib.import_module(alias.name)
                    except Exception as e:  # noqa: BLE001
                        failures.append((
                            idx, f"import {alias.name}", repr(e),
                        ))
    if failures:
        rows = "\n".join(
            f"  block #{i}: {what} → {why}" for i, what, why in failures
        )
        pytest.fail(
            f"{len(failures)} README import claim(s) don't resolve:\n"
            f"{rows}\n\n"
            f"Either restore the missing symbol to the public API, or "
            f"update README.md to match the current code."
        )


def test_readme_attribute_access_on_imported_symbols(readme):
    """For every `Symbol.attr` reference in a README python block where
    `Symbol` was just imported from a `retrotool` module, verify `.attr`
    exists on the imported object.

    Catches renames like `Rom.load → Rom.from_path` where the import line
    still resolves but the documented method is gone. Conservative: only
    checks `Name.attr` (no chained / call-result attribute access).
    """
    failures: list[tuple[int, str, str]] = []
    for idx, block in _python_blocks(readme):
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue
        sym_to_obj: dict[str, object] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if not mod.startswith("retrotool"):
                    continue
                try:
                    m = importlib.import_module(mod)
                except Exception:  # noqa: BLE001
                    continue
                for alias in node.names:
                    key = alias.asname or alias.name
                    if hasattr(m, alias.name):
                        sym_to_obj[key] = getattr(m, alias.name)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in sym_to_obj
            ):
                obj = sym_to_obj[node.value.id]
                if not hasattr(obj, node.attr):
                    failures.append((
                        idx,
                        f"{node.value.id}.{node.attr}",
                        f"{type(obj).__name__} has no attribute {node.attr!r}",
                    ))
    if failures:
        rows = "\n".join(
            f"  block #{i}: {what} — {why}" for i, what, why in failures
        )
        pytest.fail(
            f"{len(failures)} README attribute reference(s) no longer exist:\n"
            f"{rows}"
        )

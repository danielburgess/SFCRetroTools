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

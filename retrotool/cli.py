"""retrotool CLI entry point.

Subcommands:

    retrotool build <path> [-o rom] [--no-cache] [--diff ips|xdelta|both]
    retrotool extract <path> [--dest DIR]
    retrotool migrate <path> [--in-place]
    retrotool libsfx scaffold <dir> [--template NAME]
    retrotool libsfx build [<dir>] [--debug 0|1|2] [-o out.sfc]
    retrotool libsfx info <dir>
    retrotool libsfx clean <dir>

<path> may be a `.mbxml`, a `.toml`, or a directory containing either
(project.toml takes precedence over `*.mbxml` when both exist).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def _cmd_scaffold(args: argparse.Namespace) -> int:
    from retrotool.asm.libsfx import scaffold_libsfx_project
    dest = scaffold_libsfx_project(Path(args.dir), template=args.template)
    print(f"scaffolded {args.template!r} into {dest}")
    return 0


def _load_project(root: Path):
    from retrotool.asm.libsfx import LibSFXProject
    return LibSFXProject.discover(root)


def _cmd_build(args: argparse.Namespace) -> int:
    proj = _load_project(Path(args.dir))
    if args.debug is not None:
        proj.cfg.debug = args.debug
    out = Path(args.output) if args.output else None
    result = proj.build(out_rom=out)
    print(f"rom:       {result.rom}")
    print(f"size:      {result.rom.stat().st_size} bytes")
    print(f"checksum:  ${result.header.checksum_after:04X} (valid={result.header.is_valid})")
    print(f"duration:  {result.duration_ms} ms")
    if result.symfile:
        print(f"symfile:   {result.symfile}")
    if result.mapfile:
        print(f"mapfile:   {result.mapfile}")
    if result.breakpoints:
        print(f"mesen .bp: {result.breakpoints}")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    proj = _load_project(Path(args.dir))
    srcs = proj.sources()
    print(f"root:       {proj.root}")
    print(f"name:       {proj.cfg.name}")
    print(f"debug:      {proj.cfg.debug}")
    print(f"map_config: {proj.cfg.map_config}")
    print(f"obj_dir:    {proj.cfg.obj_dir}")
    for kind, paths in srcs.items():
        print(f"{kind} sources ({len(paths)}):")
        for p in paths:
            print(f"  {p.relative_to(proj.root) if p.is_relative_to(proj.root) else p}")
    return 0


def _cmd_clean(args: argparse.Namespace) -> int:
    proj = _load_project(Path(args.dir))
    proj.clean()
    rom = proj.root / f"{proj.cfg.name}.sfc"
    if rom.exists():
        rom.unlink()
    cache = proj.root / ".cache"
    if args.full and cache.exists():
        shutil.rmtree(cache)
    print(f"cleaned {proj.root}")
    return 0


# ---- mbuild dispatch ------------------------------------------------------

def _resolve_spec_path(path: Path) -> tuple[Path, str]:
    """Return (file, kind) where kind in {"mbxml","toml"}.

    If `path` is a directory, prefer `project.toml`, then the single `*.mbxml`
    inside it. Raises if ambiguous or nothing found.
    """
    if path.is_dir():
        toml = path / "project.toml"
        if toml.exists():
            return toml, "toml"
        mbxmls = sorted(path.glob("*.mbxml"))
        if len(mbxmls) == 1:
            return mbxmls[0], "mbxml"
        if not mbxmls:
            raise FileNotFoundError(
                f"no project.toml or *.mbxml found in {path}"
            )
        raise FileNotFoundError(
            f"multiple *.mbxml in {path}; pass one explicitly"
        )
    if not path.exists():
        raise FileNotFoundError(path)
    suf = path.suffix.lower()
    if suf in (".mbxml", ".xml"):
        return path, "mbxml"
    if suf == ".toml":
        return path, "toml"
    raise ValueError(f"unrecognized spec file extension: {path.suffix!r}")


def _load_spec(
    path: Path,
    *,
    defines: Optional[dict[str, str]] = None,
    defer_datadefs: bool = False,
):
    """Parse spec. When `defer_datadefs=True`, returns (spec, spec_file,
    finalize) — caller invokes `finalize()` after mutating spec.en_data_dir
    to pick a datadef resolution root (used by `extract --lang`)."""
    from retrotool.build import parse_mbxml, parse_project_toml, apply_datadefs_to_spec
    spec_file, kind = _resolve_spec_path(path)
    if kind == "mbxml":
        spec = parse_mbxml(spec_file, defines=defines)
        if defer_datadefs:
            return spec, spec_file, (lambda: None)
        return spec, spec_file
    spec = parse_project_toml(spec_file, defines=defines)
    # Auto-include DataDefs with a `[section]` sub-table from `data_dirs`.
    # Inline `[[rom.build.sections]]` and DataDef-derived sections are merged
    # and ordered (by offset, or by `[rom.build].order = [...]`). A section
    # is always defined in exactly one place — no cross-file duplication.
    from retrotool.project.loader import load_project, load_datadefs
    try:
        project = load_project(spec_file)
    except (FileNotFoundError, ValueError):
        project = None  # bare TOML without `[rom]` — inline-only build

    def _finalize():
        if project is not None and project.data_dirs:
            datadefs = load_datadefs(project)
            apply_datadefs_to_spec(spec, datadefs, order=spec.order)
        elif spec.order:
            apply_datadefs_to_spec(spec, [], order=spec.order)

    if defer_datadefs:
        return spec, spec_file, _finalize
    _finalize()
    return spec, spec_file


def _parse_defines(pairs: Optional[list[str]]) -> dict[str, str]:
    """Parse `-D name=value` flags. Multiple `-D` accepted; last value wins
    on duplicate keys. Raises SystemExit for malformed entries."""
    out: dict[str, str] = {}
    for item in pairs or ():
        if "=" not in item:
            raise SystemExit(f"-D expects name=value, got: {item!r}")
        k, _, v = item.partition("=")
        k = k.strip()
        if not k:
            raise SystemExit(f"-D expects non-empty name, got: {item!r}")
        out[k] = v
    return out


def _split_csv(s: Optional[str]) -> Optional[set[str]]:
    if not s:
        return None
    return {x.strip() for x in s.split(",") if x.strip()}


def _cmd_mbuild_build(args: argparse.Namespace) -> int:
    from retrotool.core.cache import BuildCache
    from retrotool.build import build
    from retrotool.build.reporter import make_reporter
    spec, spec_file = _load_spec(
        Path(args.path), defines=_parse_defines(args.define),
    )
    if args.diff is not None:
        spec.diff = args.diff
    source_root = spec_file.parent
    if args.output:
        out = Path(args.output)
    else:
        name = spec.name or spec_file.stem
        out = source_root / f"{name}.sfc"
    cache = None if args.no_cache else BuildCache(source_root / ".cache")
    # CLI `-j` wins; otherwise consult spec.jobs from project.toml/MBXML;
    # otherwise leave None and let the driver pick its default (serial).
    # `0` (CLI or spec) means "auto" → os.cpu_count().
    resolved_jobs = args.jobs if args.jobs is not None else spec.jobs
    # Pick a reporter: TTY → animated braille spinner; else line-per-event log.
    # `--no-progress` forces silent (None reporter — build runs without UI).
    if args.no_progress:
        reporter = None
    else:
        reporter = make_reporter(
            animate=(None if args.progress is None else args.progress),
            stream=sys.stderr,
        )
    with reporter or _NullCm():
        result = build(
            spec, source_root=source_root, out_path=out, cache=cache,
            only=_split_csv(args.only), skip=_split_csv(args.skip),
            parallel=resolved_jobs, reporter=reporter,
        )
    print(f"rom:       {result.rom_path}")
    print(f"size:      {result.rom_size} bytes")
    if result.checksum is not None:
        print(f"checksum:  ${result.checksum:04X}")
    print(f"sections:  {len(result.sections)} "
          f"(cache hits: {result.cache_hits}, skipped: {len(result.skipped)})")
    for d in result.diffs:
        if d.skipped:
            print(f"diff:      {d.format} skipped — {d.note}")
        else:
            print(f"diff:      {d.format} → {d.path} ({d.size} bytes)")
    if resolved_jobs is None:
        workers = 1
    elif resolved_jobs == 0:
        workers = os.cpu_count() or 1
    else:
        workers = max(1, resolved_jobs)
    print(f"workers:   {workers}{' (serial)' if workers == 1 else ''}")
    print(f"duration:  {result.duration_ms} ms")
    print(f"finished:  {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}")
    return 0


class _NullCm:
    """No-op context manager used when `reporter is None` so the same `with`
    block handles both progress-on and progress-off paths."""
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _cmd_mbuild_extract(args: argparse.Namespace) -> int:
    from retrotool.build import extract
    spec, spec_file, finalize = _load_spec(
        Path(args.path), defines=_parse_defines(args.define),
        defer_datadefs=True,
    )
    source_root = spec_file.parent

    # Resolve extract destination. Precedence:
    #   --dest (absolute path, overrides everything)
    #   --lang X  → spec.data_dirs_by_lang[X] is spliced into spec.en_data_dir
    #              so DataDef file= auto-defaults land there.
    #   [extract].default_lang in project.toml → same behavior as --lang.
    #   Otherwise → error. Extract must be explicit; silent defaults have
    #   clobbered translation files in the past.
    dest = Path(args.dest).resolve() if args.dest else None
    lang = args.lang
    if not dest and not lang:
        default_lang = spec.extract_config.get("default_lang")
        if isinstance(default_lang, str) and default_lang:
            lang = default_lang
    if not dest and not lang:
        sys.stderr.write(
            "error: extract requires an explicit destination.\n"
            "  pass --lang <code>  (resolved via `<code>_data_dir=` in project.toml)\n"
            "  or --dest <path>    (absolute override)\n"
            "  or set `[extract].default_lang = \"<code>\"` in the spec.\n"
        )
        return 2
    if lang:
        lang_key = lang.lower()
        dir_ = spec.data_dirs_by_lang.get(lang_key)
        if not dir_:
            known = sorted(spec.data_dirs_by_lang.keys())
            sys.stderr.write(
                f"error: --lang {lang!r}: no `{lang_key}_data_dir=` scalar in "
                f"project.toml (known langs: {known})\n"
            )
            return 2
        # Splice the chosen lang dir in as the file-autodefault so all DataDef
        # sections resolve under it.
        spec.en_data_dir = dir_

    # Resolve datadef sections now that en_data_dir reflects the chosen lang.
    finalize()

    confirm = _build_overwrite_confirmer(assume_yes=args.yes)

    try:
        result = extract(
            spec, source_root=source_root, dest_root=dest,
            only=_split_csv(args.only), skip=_split_csv(args.skip),
            confirm_existing=confirm,
        )
    except Exception as e:
        # Abort from confirm callback surfaces as HandlerError; keep CLI terse.
        sys.stderr.write(f"error: {e}\n")
        return 1
    total = sum(s.bytes_read for s in result.sections)
    print(f"sections:  {len(result.sections)}")
    print(f"bytes:     {total}")
    print(f"duration:  {result.duration_ms} ms")
    return 0


def _build_overwrite_confirmer(*, assume_yes: bool):
    """Return a `confirm_existing(paths) -> bool` for retrotool.build.extract.

    `assume_yes`: bypass prompt (used by --yes/-y).
    Non-interactive stdin: refuse overwrite (safe default for scripts/CI)."""
    def _confirm(existing: list[Path]) -> bool:
        if assume_yes:
            return True
        count = len(existing)
        sys.stderr.write(
            f"\nWARNING: extract would overwrite {count} existing file(s):\n"
        )
        preview = existing[:10]
        for p in preview:
            sys.stderr.write(f"  {p}\n")
        if count > len(preview):
            sys.stderr.write(f"  ... ({count - len(preview)} more)\n")
        if not sys.stdin.isatty():
            sys.stderr.write(
                "stdin is not a TTY — refusing to overwrite. "
                "Re-run with --yes to confirm.\n"
            )
            return False
        sys.stderr.write("Proceed with overwrite? [y/N] ")
        sys.stderr.flush()
        reply = sys.stdin.readline().strip().lower()
        return reply in ("y", "yes")
    return _confirm


def _cmd_mbuild_migrate(args: argparse.Namespace) -> int:
    from retrotool.build import migrate_mbxml
    path = Path(args.path)
    if path.is_dir() or path.suffix.lower() not in (".mbxml", ".xml"):
        raise ValueError("migrate requires an .mbxml/.xml file")
    text = migrate_mbxml(path, in_place=args.in_place)
    if args.in_place:
        print(f"migrated → {path} (backup: {path}.bak)")
    else:
        sys.stdout.write(text)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="retrotool", description="SNES ROM hacking toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)

    # build
    bb = sub.add_parser("build", help="build a ROM from an .mbxml / project.toml spec")
    bb.add_argument("path", help=".mbxml file, .toml file, or directory containing one")
    bb.add_argument("-o", "--output", help="output ROM path (default: <name>.sfc next to spec)")
    bb.add_argument("--no-cache", action="store_true", help="disable per-section BuildCache")
    bb.add_argument("--diff", choices=["ips", "xdelta", "both"], default=None,
                    help="override spec diff setting")
    bb.add_argument("--only", default=None,
                    help="comma-separated section kinds OR names to run "
                         "(e.g. asar,script,scene-desc-name)")
    bb.add_argument("--skip", default=None,
                    help="comma-separated section kinds OR names to skip")
    bb.add_argument("-j", "--jobs", type=int, default=None,
                    help="gather-phase worker thread count. Default: 1 "
                         "(serial) — overridable via [rom.build].jobs in "
                         "project.toml or jobs= on <build> in MBXML. "
                         "Pass 0 for os.cpu_count() (auto). CLI value wins "
                         "over spec value.")
    bb.add_argument("--progress", dest="progress", action="store_true",
                    default=None,
                    help="force the animated braille progress reporter even "
                         "when stderr is not a TTY")
    bb.add_argument("--no-progress", action="store_true",
                    help="disable the progress reporter entirely")
    bb.add_argument("-D", "--define", action="append", default=None,
                    metavar="NAME=VALUE",
                    help="override a spec variable (e.g. -D version=en); "
                         "repeatable. Applies to both MBXML and TOML front-ends.")
    bb.set_defaults(func=_cmd_mbuild_build)

    # extract
    ex = sub.add_parser("extract", help="extract ROM data to files per spec")
    ex.add_argument("path", help=".mbxml file, .toml file, or directory containing one")
    ex.add_argument("--lang", default=None,
                    help="language code (resolved via `<code>_data_dir=` in "
                         "project.toml, e.g. en, jp). Mutually exclusive with --dest.")
    ex.add_argument("--dest",
                    help="destination root (absolute override). Mutually exclusive "
                         "with --lang. Extract is explicit — one of --lang / --dest "
                         "is required unless `[extract].default_lang` is set in the spec.")
    ex.add_argument("-y", "--yes", action="store_true",
                    help="skip the interactive confirmation prompt when extraction "
                         "would overwrite existing files.")
    ex.add_argument("--only", default=None,
                    help="comma-separated section kinds OR names to extract")
    ex.add_argument("--skip", default=None,
                    help="comma-separated section kinds OR names to skip")
    ex.add_argument("-D", "--define", action="append", default=None,
                    metavar="NAME=VALUE",
                    help="override a spec variable (e.g. -D version=en); "
                         "repeatable.")
    ex.set_defaults(func=_cmd_mbuild_extract)

    # migrate
    mg = sub.add_parser("migrate", help="rewrite legacy MBuild 1.29 elements to unified form")
    mg.add_argument("path", help=".mbxml file")
    mg.add_argument("--in-place", action="store_true",
                    help="rewrite file (default: print migrated XML to stdout)")
    mg.set_defaults(func=_cmd_mbuild_migrate)

    libsfx = sub.add_parser("libsfx", help="libSFX assembly project commands")
    lsub = libsfx.add_subparsers(dest="libsfx_cmd", required=True)

    sp = lsub.add_parser("scaffold", help="create a new libSFX project from a template")
    sp.add_argument("dir", help="destination directory (must not exist or be empty)")
    sp.add_argument("--template", default="Template", help="libSFX example template name")
    sp.set_defaults(func=_cmd_scaffold)

    bp = lsub.add_parser("build", help="assemble + link + post-process a libSFX project")
    bp.add_argument("dir", nargs="?", default=".", help="project directory (default: .)")
    bp.add_argument("--debug", type=int, choices=[0, 1, 2], default=None)
    bp.add_argument("-o", "--output", help="output ROM path")
    bp.set_defaults(func=_cmd_build)

    ip = lsub.add_parser("info", help="print discovered sources + config")
    ip.add_argument("dir", nargs="?", default=".")
    ip.set_defaults(func=_cmd_info)

    cp = lsub.add_parser("clean", help="remove .build/ and built ROM")
    cp.add_argument("dir", nargs="?", default=".")
    cp.add_argument("--full", action="store_true", help="also wipe .cache/")
    cp.set_defaults(func=_cmd_clean)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

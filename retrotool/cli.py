"""retrotool CLI entry point — thin argparse layer over `retrotool.build`.

Each subcommand below maps 1:1 to a public function in
`retrotool.build.project`. Anything you can do via this CLI is also
reachable from Python; see :mod:`retrotool.build.project` for the
function-level API.

Subcommands:

    retrotool build <path> [-o rom] [--no-cache] [--diff ips|xdelta|both]
                           [--only NAMES] [--skip NAMES]
                           [--script-step | --script-step-batch]
                           [--script-step-progress N]
                           [-j N] [--progress|--no-progress] [-D NAME=VALUE]
    retrotool extract <path> [--lang CODE | --dest DIR] [--only/--skip ...]
    retrotool migrate <path> [--in-place]
    retrotool libsfx scaffold <dir> [--template NAME]
    retrotool libsfx build    [<dir>] [--debug 0|1|2] [-o out.sfc]
    retrotool libsfx info     <dir>
    retrotool libsfx clean    <dir> [--full]

<path> may be a `.mbxml`, a `.toml`, or a directory containing either
(project.toml takes precedence over `*.mbxml` when both exist).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional


# ---- libSFX subcommands ---------------------------------------------------

def _cmd_scaffold(args: argparse.Namespace) -> int:
    from retrotool.build import scaffold_libsfx_project
    scaffold_libsfx_project(Path(args.dir), template=args.template)
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    from retrotool.build import build_libsfx_project
    build_libsfx_project(
        Path(args.dir),
        debug=args.debug,
        output=args.output,
    )
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    from retrotool.build import info_libsfx_project
    info_libsfx_project(Path(args.dir))
    return 0


def _cmd_clean(args: argparse.Namespace) -> int:
    from retrotool.build import clean_libsfx_project
    clean_libsfx_project(Path(args.dir), full=args.full)
    return 0


# ---- mbuild subcommands ---------------------------------------------------

def _cmd_mbuild_build(args: argparse.Namespace) -> int:
    from retrotool.build import build_project

    if args.script_step or args.script_step_batch:
        return _run_script_step(args)

    progress: Optional[bool] = (
        None if args.progress is None else args.progress
    )
    build_project(
        Path(args.path),
        output=args.output,
        no_cache=args.no_cache,
        diff=args.diff,
        only=args.only,
        skip=args.skip,
        jobs=args.jobs,
        progress=progress,
        no_progress=args.no_progress,
        defines=args.define,
    )
    return 0


def _run_script_step(args: argparse.Namespace) -> int:
    """Drive `iter_step_builds()` from CLI flags.

    Interactive mode rebuilds to the same output path each step (so an
    auto-reloading emulator can pick up the change) and waits for a
    keypress between rebuilds. Batch mode emits `<stem>.stepNNN<suffix>`
    per step and exits non-interactively.
    """
    from retrotool.build import (
        SectionKind, build_project, iter_step_builds, load_spec,
        parse_csv_set, parse_defines, parse_only_args,
        default_output_path, default_cache_dir, resolve_jobs,
    )
    from retrotool.build.driver import _section_kinds_filter
    from retrotool.build.reporter import make_reporter
    from retrotool.core.cache import BuildCache

    spec, spec_file = load_spec(
        Path(args.path),
        defines=parse_defines(args.define) if args.define else None,
    )
    if args.diff is not None:
        spec.diff = args.diff
    source_root = spec_file.parent
    out = Path(args.output) if args.output else default_output_path(spec, spec_file)
    cache = None if args.no_cache else BuildCache(default_cache_dir(source_root))
    resolved_jobs = resolve_jobs(args.jobs, spec.jobs)
    only_set, script_filter = parse_only_args(parse_csv_set(args.only))
    skip_set = parse_csv_set(args.skip)

    if not only_set:
        sys.stderr.write(
            "error: --script-step / --script-step-batch require --only NAME "
            "(or --only NAME:LO-HI) to select exactly one script section\n"
        )
        return 2

    keep = _section_kinds_filter(only_set, None)
    candidates = [
        s for s in spec.sections
        if s.kind in (SectionKind.SCRIPT, SectionKind.WINDOWED_SCRIPT)
        and (keep is None or keep(s))
    ]
    if not candidates:
        sys.stderr.write(
            f"error: --script-step: no script section matches --only "
            f"{sorted(only_set)!r}\n"
        )
        return 2
    if len(candidates) > 1:
        names = [
            s.from_datadef or s.attrs.get("name") or s.source or s.kind.value
            for s in candidates
        ]
        sys.stderr.write(
            f"error: --script-step: --only matches multiple script sections "
            f"({names!r}); narrow to one\n"
        )
        return 2
    section = candidates[0]

    # Honor `--only NAME:LO-HI` if the user pre-narrowed the range.
    block_lo, block_hi = 0, None
    extra_window_range = None
    sec_id_options = {
        section.from_datadef or "",
        section.attrs.get("name") or "",
        section.attrs.get("alias") or "",
    }
    sec_id_options = {x.lower() for x in sec_id_options if x}
    if not script_filter.is_empty():
        for sid, rules in script_filter.targets_by_id.items():
            if sid not in sec_id_options:
                continue
            br = [r.block_range for r in rules if r.block_range is not None]
            wr = [r.window_range for r in rules if r.window_range is not None]
            if len(br) > 1 or len(wr) > 1:
                sys.stderr.write(
                    f"error: --script-step: multiple block/window ranges in "
                    f"--only for section {sid!r}; narrow to one\n"
                )
                return 2
            if br:
                block_lo, block_hi = br[0].lo, br[0].hi
            if wr:
                extra_window_range = wr[0]

    progress_per_step = max(1, args.script_step_progress or 1)

    if args.no_progress:
        reporter = None
    else:
        reporter = make_reporter(
            animate=(None if args.progress is None else args.progress),
            stream=sys.stderr,
        )

    if args.script_step_batch:
        namer = None  # use default <stem>.stepNNN<suffix>
    else:
        namer = lambda s, t, base: base  # noqa: E731 — overwrite same file

    print(
        f"step mode: section="
        f"{(section.from_datadef or section.attrs.get('name') or 'script')!r}"
        f" blocks={block_lo}..{block_hi if block_hi is not None else int(section.count) - 1}"
        f" progress={progress_per_step}"
    )
    print(
        "mode: " + (
            "batch (one ROM per step)"
            if args.script_step_batch
            else "interactive (Enter=advance, q=quit, j N=jump to step N)"
        )
    )

    iterator = iter_step_builds(
        spec,
        section=section,
        source_root=source_root,
        out_path=out,
        block_lo=block_lo,
        block_hi=block_hi,
        progress=progress_per_step,
        extra_window_range=extra_window_range,
        cache=cache,
        only=only_set,
        skip=skip_set,
        parallel=resolved_jobs,
        reporter=reporter,
        output_namer=namer,
    )
    if args.script_step_batch:
        with reporter or _NullCm():
            for step, total, result, step_out in iterator:
                print(
                    f"step {step}/{total}  -> {step_out.name}  "
                    f"({result.rom_size:,}b, {result.duration_ms} ms)"
                )
    else:
        with reporter or _NullCm():
            results = list(iterator)
        cur = 1
        n_steps = results[0][1] if results else 0
        while cur <= n_steps:
            step, total, result, step_out = results[cur - 1]
            print(
                f"step {step}/{total}  -> {step_out.name}  "
                f"({result.rom_size:,}b, {result.duration_ms} ms)"
            )
            nxt = _step_prompt(step, total)
            if nxt is None:
                print("aborted")
                return 0
            if nxt > total:
                break
            # Re-run from `nxt` onwards (subsequent rebuilds reuse the
            # already-computed iterator slice — but we built `results`
            # eagerly above, so just advance the index).
            cur = nxt
    return 0


class _NullCm:
    """No-op context manager so the same `with` block handles both
    progress-on and progress-off paths."""
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _step_prompt(cur: int, total: int) -> Optional[int]:
    """Ask user what to do next. Returns the next step number, or None to quit.

    Empty input → cur+1 (advance). 'q' / EOF → quit. 'j N' → jump to step N.
    """
    while True:
        try:
            sys.stderr.write(
                f"  step {cur}/{total}: [Enter]=next, q=quit, j N=jump > "
            )
            sys.stderr.flush()
            line = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            return None
        if not line:
            return None
        s = line.strip().lower()
        if s in ("q", "quit", "exit"):
            return None
        if not s:
            return cur + 1
        if s.startswith("j"):
            try:
                n = int(s[1:].strip())
            except ValueError:
                sys.stderr.write(f"  invalid jump target: {line.strip()!r}\n")
                continue
            if n < 1 or n > total:
                sys.stderr.write(f"  out of range (1..{total})\n")
                continue
            return n
        sys.stderr.write(f"  unrecognized input: {line.strip()!r}\n")


def _cmd_mbuild_extract(args: argparse.Namespace) -> int:
    from retrotool.build import extract_project
    try:
        extract_project(
            Path(args.path),
            lang=args.lang,
            dest=args.dest,
            only=args.only,
            skip=args.skip,
            defines=args.define,
            assume_yes=args.yes,
        )
    except ValueError as e:
        sys.stderr.write(f"error: {e}\n")
        return 2
    except Exception as e:  # noqa: BLE001
        # confirm-callback abort or handler error — keep CLI terse.
        sys.stderr.write(f"error: {e}\n")
        return 1
    return 0


def _cmd_mbuild_migrate(args: argparse.Namespace) -> int:
    from retrotool.build import migrate_project
    text = migrate_project(Path(args.path), in_place=args.in_place)
    if args.in_place:
        print(f"migrated → {args.path} (backup: {args.path}.bak)")
    else:
        sys.stdout.write(text)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="retrotool",
        description="SNES/SFC ROM hacking and development toolkit",
    )
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
                         "(e.g. asar,script,scene-desc-name). For script "
                         "sections, append a block selector to narrow the "
                         "build to specific entries — useful for debugging: "
                         "NAME:BLOCK, NAME:LO-HI, NAME:BLOCK:WIN, "
                         "NAME:LO-HI:WLO-WHI. Block/window selectors require "
                         "placement.mode=overflow.")
    bb.add_argument("--skip", default=None,
                    help="comma-separated section kinds OR names to skip")
    bb.add_argument("--script-step", action="store_true",
                    help="interactive successive-block build: rebuild once "
                         "per step adding `--script-step-progress` more "
                         "blocks each time, prompting Enter/q/j between "
                         "rebuilds. Requires --only NAME (or NAME:LO-HI).")
    bb.add_argument("--script-step-batch", action="store_true",
                    help="non-interactive variant of --script-step. Writes "
                         "<stem>.stepNNN.sfc per step instead of "
                         "overwriting the same file, no prompts.")
    bb.add_argument("--script-step-progress", type=int, default=1,
                    metavar="N",
                    help="block-count increment per step (default 1).")
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

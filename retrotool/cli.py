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
import shutil
import sys
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


def _load_spec(path: Path):
    from retrotool.mbuild import parse_mbxml, parse_project_toml
    spec_file, kind = _resolve_spec_path(path)
    if kind == "mbxml":
        return parse_mbxml(spec_file), spec_file
    return parse_project_toml(spec_file), spec_file


def _split_csv(s: Optional[str]) -> Optional[set[str]]:
    if not s:
        return None
    return {x.strip() for x in s.split(",") if x.strip()}


def _cmd_mbuild_build(args: argparse.Namespace) -> int:
    from retrotool.core.cache import BuildCache
    from retrotool.mbuild import build
    spec, spec_file = _load_spec(Path(args.path))
    if args.diff is not None:
        spec.diff = args.diff
    source_root = spec_file.parent
    if args.output:
        out = Path(args.output)
    else:
        name = spec.name or spec_file.stem
        out = source_root / f"{name}.sfc"
    cache = None if args.no_cache else BuildCache(source_root / ".cache")
    result = build(
        spec, source_root=source_root, out_path=out, cache=cache,
        only=_split_csv(args.only), skip=_split_csv(args.skip),
        parallel=args.jobs,
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
    print(f"duration:  {result.duration_ms} ms")
    return 0


def _cmd_mbuild_extract(args: argparse.Namespace) -> int:
    from retrotool.mbuild import extract
    spec, spec_file = _load_spec(Path(args.path))
    source_root = spec_file.parent
    dest = Path(args.dest).resolve() if args.dest else None
    result = extract(
        spec, source_root=source_root, dest_root=dest,
        only=_split_csv(args.only), skip=_split_csv(args.skip),
    )
    total = sum(s.bytes_read for s in result.sections)
    print(f"sections:  {len(result.sections)}")
    print(f"bytes:     {total}")
    print(f"duration:  {result.duration_ms} ms")
    return 0


def _cmd_mbuild_migrate(args: argparse.Namespace) -> int:
    from retrotool.mbuild import migrate_mbxml
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
                    help="comma-separated section kinds to run (e.g. asar,script)")
    bb.add_argument("--skip", default=None,
                    help="comma-separated section kinds to skip")
    bb.add_argument("-j", "--jobs", type=int, default=None,
                    help="parallel pre-encoding workers (default: serial); "
                         "1 = serial prepare phase, N = ProcessPoolExecutor cap")
    bb.set_defaults(func=_cmd_mbuild_build)

    # extract
    ex = sub.add_parser("extract", help="extract ROM data to files per spec")
    ex.add_argument("path", help=".mbxml file, .toml file, or directory containing one")
    ex.add_argument("--dest", help="destination root (default: spec dir + spec.path)")
    ex.add_argument("--only", default=None,
                    help="comma-separated section kinds to extract")
    ex.add_argument("--skip", default=None,
                    help="comma-separated section kinds to skip")
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

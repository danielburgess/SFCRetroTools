"""Project-level facade — Python entry points that mirror the CLI subcommands.

The CLI is a thin argparse layer on top of this module: every `retrotool <cmd>`
invocation has a corresponding `build_project()` / `extract_project()` /
`migrate_project()` function with the same defaults, the same output, and the
same return type. This lets you drive retrotool from a notebook, a Python
build script, or a higher-level orchestrator without re-implementing the
spec-discovery / output-path / cache / reporter wiring the CLI does.

Example::

    from retrotool.build import build_project
    result = build_project("my-game/", diff="xdelta", jobs=4)
    print(result.rom_path, result.checksum)

When called with the defaults a `build_project()` invocation prints the same
progress reporter + summary lines as `retrotool build`. Pass
`print_summary=False` (and/or `progress=False`) to silence it for programmatic
use that just wants the `BuildResult`.
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import (
    Callable, IO, Iterator, Optional, Union,
)


# ---- Spec discovery + parsing ---------------------------------------------

def resolve_spec_path(path: Union[str, Path]) -> tuple[Path, str]:
    """Disambiguate a `--path` argument into `(file, kind)`.

    `kind` is `"mbxml"` or `"toml"`. If `path` is a directory, prefers
    `project.toml` over a single `*.mbxml`. Raises `FileNotFoundError` for
    empty / ambiguous directories or `ValueError` for unrecognized
    extensions.
    """
    p = Path(path)
    if p.is_dir():
        toml = p / "project.toml"
        if toml.exists():
            return toml, "toml"
        mbxmls = sorted(p.glob("*.mbxml"))
        if len(mbxmls) == 1:
            return mbxmls[0], "mbxml"
        if not mbxmls:
            raise FileNotFoundError(
                f"no project.toml or *.mbxml found in {p}"
            )
        raise FileNotFoundError(
            f"multiple *.mbxml in {p}; pass one explicitly"
        )
    if not p.exists():
        raise FileNotFoundError(p)
    suf = p.suffix.lower()
    if suf in (".mbxml", ".xml"):
        return p, "mbxml"
    if suf == ".toml":
        return p, "toml"
    raise ValueError(f"unrecognized spec file extension: {p.suffix!r}")


def load_spec(
    path: Union[str, Path],
    *,
    defines: Optional[dict[str, str]] = None,
    defer_datadefs: bool = False,
):
    """Parse a spec file (or directory) and return `(spec, spec_file)`.

    Mirrors the work `retrotool build` does on its `<path>` argument:
    front-end dispatch (MBXML vs project.toml), DataDef resolution from
    `data_dirs`, optional deferral so the caller can mutate
    `spec.en_data_dir` before datadefs land (used by `extract --lang`).

    With `defer_datadefs=True`, returns `(spec, spec_file, finalize)`
    where `finalize()` applies datadefs after the caller is done mutating
    the spec.
    """
    # Deferred imports keep `retrotool.build` cheap to import for callers
    # that only want, e.g., `BuildResult`.
    from retrotool.build import (
        parse_mbxml, parse_project_toml, apply_datadefs_to_spec,
    )
    spec_file, kind = resolve_spec_path(path)
    if kind == "mbxml":
        spec = parse_mbxml(spec_file, defines=defines)
        if defer_datadefs:
            return spec, spec_file, (lambda: None)
        return spec, spec_file
    spec = parse_project_toml(spec_file, defines=defines)
    from retrotool.project.loader import load_project, load_datadefs
    try:
        project = load_project(spec_file)
    except (FileNotFoundError, ValueError):
        project = None  # bare TOML without [rom] — inline-only build

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


# ---- CLI-shaped argument coercion -----------------------------------------

def parse_defines(pairs: Optional[list[str]]) -> dict[str, str]:
    """Parse a list of `name=value` strings — same semantics as `-D` on CLI.

    Last value wins on duplicate keys. Raises `ValueError` for malformed
    entries (no `=`, empty key).
    """
    out: dict[str, str] = {}
    for item in pairs or ():
        if "=" not in item:
            raise ValueError(f"-D expects name=value, got: {item!r}")
        k, _, v = item.partition("=")
        k = k.strip()
        if not k:
            raise ValueError(f"-D expects non-empty name, got: {item!r}")
        out[k] = v
    return out


def parse_csv_set(s: Union[str, set[str], list[str], None]) -> Optional[set[str]]:
    """Coerce `--only` / `--skip` style input into a `set[str]` (or None).

    Accepts a CSV string, a list, or an already-formed set. Empty / None
    returns None so callers can pass through to `build()` unchanged.
    """
    if s is None:
        return None
    if isinstance(s, set):
        return s if s else None
    if isinstance(s, (list, tuple)):
        out = {x.strip() for x in s if x and x.strip()}
        return out or None
    out = {x.strip() for x in str(s).split(",") if x.strip()}
    return out or None


# ---- Defaults the CLI computes; library callers want them too -------------

def default_output_path(spec, spec_file: Path) -> Path:
    """Default output: `<spec.name or spec.stem>.sfc`.

    Honors `[rom.build].output_dir` when set — the ROM lands in
    `<spec_dir>/<output_dir>/<name>.sfc` (an absolute `output_dir` is used
    as-is). Otherwise the file is placed next to the spec file, preserving
    the original behavior. An explicit `output=` to `build_project()` always
    overrides this.
    """
    name = spec.name or spec_file.stem
    out_dir = getattr(spec, "output_dir", None)
    base = spec_file.parent / out_dir if out_dir else spec_file.parent
    return base / f"{name}.sfc"


def default_cache_dir(source_root: Path) -> Path:
    """Default per-section cache location — `<source_root>/.cache`."""
    return Path(source_root) / ".cache"


def resolve_jobs(cli_jobs: Optional[int], spec_jobs: Optional[int]) -> Optional[int]:
    """Pick the effective worker count: CLI `-j` wins, otherwise consult
    `spec.jobs` (from `[rom.build].jobs` / `<build jobs="…">`), otherwise
    `None` (driver default = serial). `0` means auto (`os.cpu_count()`).
    """
    return cli_jobs if cli_jobs is not None else spec_jobs


def workers_for_print(resolved_jobs: Optional[int]) -> int:
    """Same calculation `retrotool build` does for the `workers:` summary
    line. Helpful when a library caller wants the CLI's printed count.
    """
    if resolved_jobs is None:
        return 1
    if resolved_jobs == 0:
        return os.cpu_count() or 1
    return max(1, resolved_jobs)


# ---- extract destination resolution ---------------------------------------

def resolve_extract_dest(
    spec,
    *,
    lang: Optional[str] = None,
    dest: Union[str, Path, None] = None,
) -> tuple[Optional[Path], Optional[str]]:
    """Apply the `--lang` / `--dest` / `[extract].default_lang` precedence
    used by `retrotool extract`.

    Returns `(dest_path_or_none, resolved_lang_or_none)`. When `lang` wins,
    splices `spec.data_dirs_by_lang[lang]` into `spec.en_data_dir` so
    DataDef `file=` auto-defaults land under that root (mirrors CLI).

    Raises `ValueError` when neither `lang` nor `dest` is supplied and the
    spec has no `[extract].default_lang`. Raises with a readable list of
    known langs when `--lang` doesn't match anything.
    """
    if dest is not None:
        return Path(dest).resolve(), None
    if not lang:
        default_lang = spec.extract_config.get("default_lang")
        if isinstance(default_lang, str) and default_lang:
            lang = default_lang
    if not lang:
        raise ValueError(
            "extract requires an explicit destination: pass lang= "
            "(via project.toml's <code>_data_dir=) or dest= (absolute "
            "override), or set [extract].default_lang in the spec"
        )
    lang_key = lang.lower()
    dir_ = spec.data_dirs_by_lang.get(lang_key)
    if not dir_:
        known = sorted(spec.data_dirs_by_lang.keys())
        raise ValueError(
            f"lang {lang!r}: no `{lang_key}_data_dir=` scalar in "
            f"project.toml (known langs: {known})"
        )
    spec.en_data_dir = dir_
    return None, lang_key


def make_overwrite_confirmer(*, assume_yes: bool, stream: Optional[IO[str]] = None):
    """Build a `confirm_existing(paths) -> bool` for `extract()`.

    Mirrors CLI behavior: prompt on TTY, refuse on non-TTY (safe default),
    bypass entirely with `assume_yes=True`. Library users typically pass
    `assume_yes=True` for non-interactive scripts; pass `False` and a
    custom `stream` for a pseudo-interactive flow.
    """
    out = stream if stream is not None else sys.stderr

    def _confirm(existing: list[Path]) -> bool:
        if assume_yes:
            return True
        count = len(existing)
        out.write(
            f"\nWARNING: extract would overwrite {count} existing file(s):\n"
        )
        preview = existing[:10]
        for p in preview:
            out.write(f"  {p}\n")
        if count > len(preview):
            out.write(f"  ... ({count - len(preview)} more)\n")
        if not sys.stdin.isatty():
            out.write(
                "stdin is not a TTY — refusing to overwrite. "
                "Re-run with assume_yes=True to confirm.\n"
            )
            return False
        out.write("Proceed with overwrite? [y/N] ")
        out.flush()
        reply = sys.stdin.readline().strip().lower()
        return reply in ("y", "yes")
    return _confirm


# ---- Project-level facade — full CLI equivalents --------------------------

class _NullCm:
    """No-op context manager for the optional progress reporter."""
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _resolve_stream(stream: Optional[IO[str]], default_attr: str) -> IO[str]:
    """Resolve a stream argument at call time, not at function-definition
    time, so test harnesses (e.g. pytest's `capsys`) that monkey-patch
    `sys.stdout` / `sys.stderr` after this module is imported still see
    the redirected stream. `default_attr` is `"stdout"` or `"stderr"`.
    """
    if stream is not None:
        return stream
    return getattr(sys, default_attr)


def _print_build_summary(
    result, *, resolved_jobs: Optional[int], stream: IO[str],
) -> None:
    """Emit the `retrotool build` stdout summary block.

    Kept separate so callers wanting a non-default summary can suppress
    this and write their own from `result`.
    """
    print(f"rom:       {result.rom_path}", file=stream)
    print(f"size:      {result.rom_size} bytes", file=stream)
    if result.checksum is not None:
        print(f"checksum:  ${result.checksum:04X}", file=stream)
    print(
        f"sections:  {len(result.sections)} "
        f"(cache hits: {result.cache_hits}, skipped: {len(result.skipped)})",
        file=stream,
    )
    for d in result.diffs:
        if d.skipped:
            print(f"diff:      {d.format} skipped — {d.note}", file=stream)
        else:
            print(
                f"diff:      {d.format} → {d.path} ({d.size} bytes)",
                file=stream,
            )
    workers = workers_for_print(resolved_jobs)
    print(
        f"workers:   {workers}{' (serial)' if workers == 1 else ''}",
        file=stream,
    )
    print(f"duration:  {result.duration_ms} ms", file=stream)
    print(
        f"finished:  {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}",
        file=stream,
    )


def build_project(
    path: Union[str, Path],
    *,
    output: Union[str, Path, None] = None,
    no_cache: bool = False,
    diff: Optional[str] = None,
    only: Union[str, set[str], list[str], None] = None,
    skip: Union[str, set[str], list[str], None] = None,
    jobs: Optional[int] = None,
    progress: Optional[bool] = None,
    no_progress: bool = False,
    defines: Union[dict[str, str], list[str], None] = None,
    print_summary: bool = True,
    summary_stream: Optional[IO[str]] = None,
    progress_stream: Optional[IO[str]] = None,
):
    """End-to-end equivalent of `retrotool build <path>`.

    Discovers the spec under `path`, resolves output / cache / jobs /
    reporter the same way the CLI does, runs the build, and (by default)
    prints the same stdout summary. Returns the `BuildResult`.

    All keyword arguments mirror their CLI counterparts:

    * `output`        — explicit output path; defaults to
      `<spec.name or spec.stem>.sfc` next to the spec.
    * `no_cache`      — disable the per-section `BuildCache`.
    * `diff`          — override `spec.diff` (`"ips"` / `"xdelta"` /
      `"both"`).
    * `only` / `skip` — section filters (CSV string, list, or set).
      `only` honors the script-block selector syntax documented for the
      CLI: e.g. `"main_dialog:42"`, `"main_dialog:42-50:0-3"`.
    * `jobs`          — gather-phase worker count override.
    * `progress`      — `True`/`False` to force the animated reporter
      on/off; `None` means TTY-detect.
    * `no_progress`   — disable the reporter entirely (silent build).
    * `defines`       — `-D NAME=VALUE` overrides; accepts a dict or
      a list of `"name=value"` strings.
    * `print_summary` — set False for a quiet library call.

    Step mode is exposed via :func:`build_project_steps` /
    :func:`iter_step_builds` — kept separate so this entry point has a
    single return type.
    """
    from retrotool.core.cache import BuildCache
    from retrotool.build import build, parse_only_args
    from retrotool.build.reporter import make_reporter

    if isinstance(defines, dict):
        defines_dict = dict(defines)
    elif defines:
        defines_dict = parse_defines(list(defines))
    else:
        defines_dict = None

    spec, spec_file = load_spec(path, defines=defines_dict)
    if diff is not None:
        spec.diff = diff
    source_root = spec_file.parent
    out = Path(output) if output else default_output_path(spec, spec_file)
    out.parent.mkdir(parents=True, exist_ok=True)  # ensure [rom.build].output_dir exists
    cache = None if no_cache else BuildCache(default_cache_dir(source_root))
    resolved_jobs = resolve_jobs(jobs, spec.jobs)
    summary_out = _resolve_stream(summary_stream, "stdout")
    progress_out = _resolve_stream(progress_stream, "stderr")

    reporter = (
        None if no_progress
        else make_reporter(animate=progress, stream=progress_out)
    )

    only_set, script_filter = parse_only_args(parse_csv_set(only))
    skip_set = parse_csv_set(skip)

    with reporter or _NullCm():
        result = build(
            spec, source_root=source_root, out_path=out, cache=cache,
            only=only_set, skip=skip_set,
            parallel=resolved_jobs, reporter=reporter,
            script_filter=script_filter if not script_filter.is_empty() else None,
        )
    if print_summary:
        _print_build_summary(
            result, resolved_jobs=resolved_jobs, stream=summary_out,
        )
    return result


# ---- Step-mode iteration --------------------------------------------------

def iter_step_builds(
    spec,
    *,
    section,
    source_root: Path,
    out_path: Path,
    block_lo: int = 0,
    block_hi: Optional[int] = None,
    progress: int = 1,
    extra_window_range=None,
    cache=None,
    only: Optional[set[str]] = None,
    skip: Optional[set[str]] = None,
    parallel: Optional[int] = None,
    reporter=None,
    output_namer: Optional[Callable[[int, int, Path], Path]] = None,
) -> Iterator[tuple[int, int, "BuildResult", Path]]:
    """Yield one `(step_idx, total_steps, BuildResult, output_path)` per
    successive (cumulative) block range.

    `section` must be a single matched script section (use
    `_section_kinds_filter` or pre-filter `spec.sections`). Yields:
      * step 1 → blocks [block_lo .. block_lo + progress - 1]
      * step 2 → blocks [block_lo .. block_lo + 2*progress - 1]
      * …
      * step N → blocks [block_lo .. block_hi]

    `output_namer(step_idx, total, base_path) -> Path` overrides the
    default `<stem>.stepNNN<suffix>` naming used by `--script-step-batch`.
    Pass a no-op (returning the same path) to overwrite a single ROM each
    step, mirroring the interactive CLI mode.
    """
    from retrotool.build import (
        IndexRange, ScriptFilter, ScriptTarget, build,
    )

    if section.count is None:
        raise ValueError(
            f"section {section.source!r} has no count= (can't enumerate blocks)"
        )
    total = int(section.count)
    if block_hi is None:
        block_hi = total - 1
    block_hi = min(block_hi, total - 1)
    if block_lo > block_hi:
        raise ValueError(f"empty block range {block_lo}..{block_hi}")
    progress = max(1, progress)
    n_steps = ((block_hi - block_lo) // progress) + 1

    section_id = (
        section.from_datadef
        or section.attrs.get("name")
        or section.attrs.get("alias")
        or ""
    )
    if not section_id:
        raise ValueError(
            f"section {section.source!r} has no usable name; set "
            f"[section.name] or use --only with a datadef name"
        )

    def _default_namer(step_idx: int, total_n: int, base: Path) -> Path:
        return base.with_name(f"{base.stem}.step{step_idx:03d}{base.suffix}")
    namer = output_namer or _default_namer

    for step in range(1, n_steps + 1):
        cur_hi = min(block_lo + step * progress - 1, block_hi)
        sf = ScriptFilter()
        sf.add(ScriptTarget(
            section_id=section_id,
            block_range=IndexRange(block_lo, cur_hi),
            window_range=extra_window_range,
        ))
        step_out = namer(step, n_steps, out_path)
        result = build(
            spec, source_root=source_root, out_path=step_out, cache=cache,
            only=only, skip=skip,
            parallel=parallel, reporter=reporter,
            script_filter=sf,
        )
        yield step, n_steps, result, step_out


# ---- Migrate / extract project facades -----------------------------------

def extract_project(
    path: Union[str, Path],
    *,
    lang: Optional[str] = None,
    dest: Union[str, Path, None] = None,
    only: Union[str, set[str], list[str], None] = None,
    skip: Union[str, set[str], list[str], None] = None,
    defines: Union[dict[str, str], list[str], None] = None,
    assume_yes: bool = False,
    print_summary: bool = True,
    summary_stream: Optional[IO[str]] = None,
):
    """End-to-end equivalent of `retrotool extract <path>`.

    Same semantics: needs one of `lang=`, `dest=`, or
    `[extract].default_lang` in the spec. Returns the `ExtractResult`.
    Set `assume_yes=True` for non-interactive overwriting; otherwise the
    overwrite prompt mirrors the CLI (refuses on non-TTY).
    """
    from retrotool.build import extract

    if isinstance(defines, dict):
        defines_dict = dict(defines)
    elif defines:
        defines_dict = parse_defines(list(defines))
    else:
        defines_dict = None

    spec, spec_file, finalize = load_spec(
        path, defines=defines_dict, defer_datadefs=True,
    )
    source_root = spec_file.parent
    resolved_dest, _resolved_lang = resolve_extract_dest(
        spec, lang=lang, dest=dest,
    )
    finalize()

    confirm = make_overwrite_confirmer(assume_yes=assume_yes)
    result = extract(
        spec, source_root=source_root, dest_root=resolved_dest,
        only=parse_csv_set(only), skip=parse_csv_set(skip),
        confirm_existing=confirm,
    )
    if print_summary:
        out = _resolve_stream(summary_stream, "stdout")
        total = sum(s.bytes_read for s in result.sections)
        print(f"sections:  {len(result.sections)}", file=out)
        print(f"bytes:     {total}", file=out)
        print(f"duration:  {result.duration_ms} ms", file=out)
    return result


def migrate_project(
    path: Union[str, Path],
    *,
    in_place: bool = False,
) -> str:
    """End-to-end equivalent of `retrotool migrate <file>`.

    Returns the migrated XML text. With `in_place=True`, also rewrites the
    file (saving a `.bak` next to it) before returning.
    """
    from retrotool.build import migrate_mbxml
    p = Path(path)
    if p.is_dir() or p.suffix.lower() not in (".mbxml", ".xml"):
        raise ValueError("migrate requires an .mbxml/.xml file")
    return migrate_mbxml(p, in_place=in_place)


# ---- libsfx project facade ------------------------------------------------

def build_libsfx_project(
    directory: Union[str, Path] = ".",
    *,
    debug: Optional[int] = None,
    output: Union[str, Path, None] = None,
    print_summary: bool = True,
    summary_stream: Optional[IO[str]] = None,
):
    """End-to-end equivalent of `retrotool libsfx build`.

    Returns the `LibSFXResult` (rom path, header, duration, optional
    symfile/mapfile/breakpoints). Prints the same summary block as the CLI
    when `print_summary=True`.
    """
    from retrotool.asm.libsfx import LibSFXProject
    proj = LibSFXProject.discover(Path(directory))
    if debug is not None:
        proj.cfg.debug = debug
    out = Path(output) if output else None
    result = proj.build(out_rom=out)
    if print_summary:
        s = _resolve_stream(summary_stream, "stdout")
        print(f"rom:       {result.rom}", file=s)
        print(f"size:      {result.rom.stat().st_size} bytes", file=s)
        print(
            f"checksum:  ${result.header.checksum_after:04X} "
            f"(valid={result.header.is_valid})", file=s,
        )
        print(f"duration:  {result.duration_ms} ms", file=s)
        if result.symfile:
            print(f"symfile:   {result.symfile}", file=s)
        if result.mapfile:
            print(f"mapfile:   {result.mapfile}", file=s)
        if result.breakpoints:
            print(f"mesen .bp: {result.breakpoints}", file=s)
    return result


def info_libsfx_project(
    directory: Union[str, Path] = ".",
    *,
    print_summary: bool = True,
    summary_stream: Optional[IO[str]] = None,
):
    """End-to-end equivalent of `retrotool libsfx info`."""
    from retrotool.asm.libsfx import LibSFXProject
    proj = LibSFXProject.discover(Path(directory))
    srcs = proj.sources()
    if print_summary:
        s = _resolve_stream(summary_stream, "stdout")
        print(f"root:       {proj.root}", file=s)
        print(f"name:       {proj.cfg.name}", file=s)
        print(f"debug:      {proj.cfg.debug}", file=s)
        print(f"map_config: {proj.cfg.map_config}", file=s)
        print(f"obj_dir:    {proj.cfg.obj_dir}", file=s)
        for kind, paths in srcs.items():
            print(f"{kind} sources ({len(paths)}):", file=s)
            for p in paths:
                rel = (
                    p.relative_to(proj.root)
                    if p.is_relative_to(proj.root) else p
                )
                print(f"  {rel}", file=s)
    return proj, srcs


def clean_libsfx_project(
    directory: Union[str, Path] = ".",
    *,
    full: bool = False,
    print_summary: bool = True,
    summary_stream: Optional[IO[str]] = None,
):
    """End-to-end equivalent of `retrotool libsfx clean` (and `--full`).

    Always runs `LibSFXProject.clean()`; deletes the built ROM next to
    the project; with `full=True` also wipes the per-project `.cache/`.
    """
    from retrotool.asm.libsfx import LibSFXProject
    proj = LibSFXProject.discover(Path(directory))
    proj.clean()
    rom = proj.root / f"{proj.cfg.name}.sfc"
    if rom.exists():
        rom.unlink()
    cache = proj.root / ".cache"
    if full and cache.exists():
        shutil.rmtree(cache)
    if print_summary:
        s = _resolve_stream(summary_stream, "stdout")
        print(f"cleaned {proj.root}", file=s)
    return proj


def scaffold_libsfx_project(
    directory: Union[str, Path],
    *,
    template: str = "Template",
    print_summary: bool = True,
    summary_stream: Optional[IO[str]] = None,
) -> Path:
    """End-to-end equivalent of `retrotool libsfx scaffold`."""
    from retrotool.asm.libsfx import scaffold_libsfx_project as _scaffold
    dest = _scaffold(Path(directory), template=template)
    if print_summary:
        s = _resolve_stream(summary_stream, "stdout")
        print(f"scaffolded {template!r} into {dest}", file=s)
    return dest

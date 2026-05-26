# retrotool

**SNES/SFC ROM hacking *and* development toolkit** — v1.0

A Python library that consolidates the tooling scattered across multiple ROM-hacking projects
into a single installable package: address math, ROM header handling, tile/palette/sprite codecs,
compression (LZSS + RLE), script dumping/insertion, pointer-table heuristics, [Mesen2-Diz](https://github.com/danielburgess/Mesen2-Diz)
debugger IPC, asar/bass/ca65 patching + linking, libSFX-driven from-scratch ROM builds, and
Godot/Tiled/C++/Python export emitters.

Built with automation in mind for **both directions** of SNES/SFC work:

- **Romhacking** — extract assets and scripts from existing ROMs, edit them, re-insert with
  byte-exact builds, diff against the original (IPS / xdelta), bisect translation regressions
  with `--only NAME:BLOCK` and step-mode rebuilds.
- **Original development** — author new SNES games from scratch via the bundled libSFX
  toolchain (ca65/ld65 + SuperFamiconv + BRRtools + lz4), drive complete project builds with
  `retrotool libsfx build`, mix-and-match assemblers per section (asar, bass, ca65), apply
  asar patches on top of a libSFX canvas, debug live via Mesen2-Diz IPC.

Static analysis, live debugger IPC, patch builds, asset export, and from-scratch assembly all
wire into a single scriptable pipeline instead of a pile of one-off tools.

v1.0 is the first stable release of the post-rewrite scope — the full toolkit (library + CLI,
example projects, and a pytest suite). The 0.1 line (address-only) still works through
compatibility shims.

## Install

```
pip install retrotool
```

Requires Python 3.12+ (uses stdlib `tomllib`).

Optional **bundled-binary extras** ship the third-party tools retrotool drives, so users don't have to chase system installs:

```
pip install retrotool[asar]      # asar patcher (LGPL-3.0+ binary)
pip install retrotool[bass]      # bass v18 ARM9 fork (ISC binary + arch tables)
pip install retrotool[libsfx]    # full Optiroc toolchain (ca65/ld65/SuperFamiconv/…)
pip install retrotool[xdelta]    # xdelta3 binary delta tool
pip install retrotool[all]       # every bundled wheel at once
```

retrotool drives a complete libSFX build end-to-end with no external toolchain install. The assembler is selectable per project: ca65 (via `libsfx`), asar (`kind="asar"`), or bass v18 (`kind="bass"`). See [libSFX assembly projects](#libsfx-assembly-projects) below and [`examples/libsfx-hello/`](examples/libsfx-hello/) for a walkthrough.

### Library-or-CLI, your call

Every capability is reachable from both a CLI subcommand **and** a plain
Python import — retrotool is a library first, a CLI second. Build your own
scripts, glue several projects together, or invoke subsystems directly from a
Jupyter notebook; nothing is hidden behind the CLI. In the same codebase you
can mix:

- **`project.toml` projects** — retrotool-native layout (`[build.libsfx]`,
  `[build]`, per-table `tables/*.toml`).
- **MBXML specs** — MBuild 1.29-compatible, auto-migrated; drop-in for
  existing projects.
- **libSFX projects** — ca65/ld65 + libSFX runtime, scaffolded or adopted.
- **asar patches** — apply on top of any of the above.
- **Raw Python** — every dataclass, codec, and handler importable; build
  pipelines that don't look like anyone else's.

> This is customizable to the max — arguably *too* customizable. That's
> intentional. The end-game is a customizable GUI on top of this library
> that manages projects, applies patches (including patch bundles pulled
> from a GitHub repo by reference), and lets you modify each game with a
> single workbench. See [Where this is going](#where-this-is-going) below.

## CLI Reference

Every subcommand is reachable as `retrotool <cmd>` after install. Each one is a thin wrapper over the same library APIs documented under [What Each Module Is For](#what-each-module-is-for) — anything the CLI does, you can do from Python.

```
retrotool build    <path> [options]    # spec → ROM
retrotool extract  <path> [options]    # ROM → source files (symmetric to build)
retrotool migrate  <file>   [options]  # MBuild 1.29 → unified retrotool form
retrotool libsfx   {scaffold|build|info|clean} [...]
```

`<path>` for `build` / `extract` may be a `.mbxml` file, a `.toml` file, or a directory containing either. When both `project.toml` and `*.mbxml` live in the same directory, `project.toml` wins.

---

### `retrotool build`

Apply a build spec to its `original=` ROM (or a `<libsfx>`-generated canvas) and write the result.

```
retrotool build <path>
                [-o, --output OUT]
                [--no-cache]
                [--diff ips|xdelta|both]
                [--only KINDS]
                [--skip KINDS]
                [--script-step | --script-step-batch]
                [--script-step-progress N]
                [-j, --jobs N]
                [--progress | --no-progress]
                [-D NAME=VALUE]...
```

| flag | description |
|---|---|
| `path` | **required.** `.mbxml` / `.toml` file or directory containing one. |
| `-o`, `--output OUT` | output ROM path. default: `<spec.name or spec.stem>.sfc` next to the spec. |
| `--no-cache` | disable the per-section `BuildCache`. forces every section to re-run its handler. |
| `--diff {ips,xdelta,both}` | override the spec's `diff=` setting. emits patches alongside the ROM. |
| `--only KINDS` | comma-separated section **kinds** *or* **names** to run; everything else lands in `result.skipped`. matches `kind.value`, `from_datadef`, `attrs.name`, `attrs.alias`, and `source` suffixes. **Script-block targeting:** append `:BLOCK[-BLOCKEND][:WIN[-WINEND]]` to a script section name to narrow the build to specific entries (debugging). E.g. `dialog-1:42`, `dialog-1:42-50`, `dialog-1:42:0-3`. Block/window selectors require `placement.mode = "overflow"`. |
| `--skip KINDS` | comma-separated kinds/names to exclude. |
| `--script-step` | interactive successive-block build. requires `--only NAME` (or `--only NAME:LO-HI`). rebuilds the ROM repeatedly with `--script-step-progress` more blocks active each step, prompting **Enter** to advance, **q** to quit, **j N** to jump to step N. pair with an emulator that auto-reloads on file change to bisect a script regression. |
| `--script-step-batch` | non-interactive variant of `--script-step`. writes one ROM per step to `<stem>.stepNNN.sfc` and exits. easier for scripted bisection or CI. |
| `--script-step-progress N` | block-count increment per step (default `1`). |
| `-j N`, `--jobs N` | gather-phase ThreadPool worker count. default = `os.cpu_count()`. `-j 1` = fully serial (debugging non-determinism). parallel-eligible kinds: `rep`, `ins`, `bin`, `graphics`, `fixed-records`, plus `<asar cache="1">` (diff-mode). |
| `--progress` | force the animated braille spinner even when stderr is not a TTY (e.g. piping through `tee`). |
| `--no-progress` | disable the progress reporter entirely. CI / log-only environments. |
| `-D NAME=VALUE`, `--define NAME=VALUE` | override a spec variable. repeatable; later wins on duplicate keys. applies to both MBXML and TOML front-ends. |

**Examples:**

```bash
# Plain build — auto-detects spec, writes <name>.sfc next to it
retrotool build my-game/

# Custom output, xdelta-only diff, 4 workers
retrotool build my-game/project.toml -o build/patched.sfc --diff xdelta -j 4

# Build only asar patches and one named script section
retrotool build my-game/ --only asar,main_dialog

# Debug a script regression — only insert one block (overflow mode):
retrotool build my-game/ --only main_dialog:42

# Same, but block range + window subset:
retrotool build my-game/ --only main_dialog:42-50:0-3

# Bisect: rebuild interactively, +1 block per step, prompt between rebuilds
retrotool build my-game/ --only main_dialog --script-step

# Same, but emit one ROM per step (main_dialog.step001.sfc, .step002.sfc, ...)
retrotool build my-game/ --only main_dialog --script-step-batch \
    --script-step-progress 16

# CI: log lines only, no animation, no cache (force fresh)
retrotool build my-game/ --no-progress --no-cache

# Multi-locale build with -D overrides
retrotool build my-game/ -D version=en -D include_credits=1 -o builds/en.sfc
retrotool build my-game/ -D version=jp                       -o builds/jp.sfc

# Debugging non-deterministic output: force serial + bypass cache
retrotool build my-game/ -j 1 --no-cache

# Force animation when stdout is captured (e.g. asciinema)
retrotool build my-game/ --progress 2> build.log
```

---

### `retrotool extract`

Extract ROM data back to source files per the spec — the symmetric counterpart to `build`. Same handler set; same section filters.

```
retrotool extract <path>
                  [--lang CODE | --dest DIR]
                  [-y, --yes]
                  [--only KINDS]
                  [--skip KINDS]
                  [-D NAME=VALUE]...
```

| flag | description |
|---|---|
| `path` | **required.** spec file or directory (same rules as `build`). |
| `--lang CODE` | language code; resolved via the `<code>_data_dir=` scalar in `project.toml` (e.g. `--lang en` reads from `en_data_dir`). DataDef `file=` auto-defaults land under that dir. **mutually exclusive with `--dest`.** |
| `--dest DIR` | absolute destination root, bypasses the lang/data_dir lookup. **mutually exclusive with `--lang`.** |
| `-y`, `--yes` | skip the interactive overwrite-confirmation prompt. extract refuses overwrite when stdin is non-TTY unless `-y` is set (safe default for CI / piped scripts). |
| `--only KINDS`, `--skip KINDS` | section filters, same syntax as `build`. |
| `-D NAME=VALUE`, `--define NAME=VALUE` | spec-variable override. repeatable. |

`extract` is **explicit** about destination — you must pass one of `--lang`, `--dest`, or set `[extract].default_lang` in the spec, otherwise it errors out. Silent defaults have clobbered translation files in the past; this guard is intentional.

**Examples:**

```bash
# Extract using project.toml [extract].default_lang
retrotool extract my-game/

# Extract Japanese sources to the JP data dir from `jp_data_dir=` in project.toml
retrotool extract my-game/ --lang jp

# Extract to a one-off directory; accept overwrites without prompting
retrotool extract my-game/ --dest /tmp/extract-preview --yes

# Only extract script + fixed-records sections
retrotool extract my-game/ --lang en --only script,fixed-records

# Re-extract with a different `version=` define (e.g. extract patched-side data)
retrotool extract my-game/ --lang en -D version=patched
```

---

### `retrotool migrate`

Rewrite a legacy MBuild 1.29 `.mbxml` into the unified retrotool form (e.g. `<lzr> → <bin codec=>`, `<bpr> → <graphics>`, `<sbr> → <script>`). One-shot tool — once a project is migrated you don't need it again.

```
retrotool migrate <file.mbxml> [--in-place]
```

| flag | description |
|---|---|
| `file.mbxml` | **required.** must be `.mbxml` or `.xml`. directories and `.toml` raise. |
| `--in-place` | rewrite the file and save a `.bak` next to it. without this flag the migrated XML prints to stdout. |

**Examples:**

```bash
# Preview the migration (stdout, file untouched)
retrotool migrate legacy.mbxml

# Inspect the diff before committing
retrotool migrate legacy.mbxml | diff legacy.mbxml -

# Rewrite in place; legacy.mbxml.bak is the backup
retrotool migrate legacy.mbxml --in-place
```

---

### `retrotool libsfx`

Subcommands for libSFX-layout assembly projects (ca65/ld65 + libSFX runtime). Requires the [`retrotool[libsfx]` extra](#libsfx-assembly-projects), which bundles the full Optiroc toolchain as prebuilt binaries.

#### `retrotool libsfx scaffold <dir>`

Create a fresh libSFX project from a bundled template.

| flag | description |
|---|---|
| `dir` | **required.** destination directory. must not exist or must be empty. |
| `--template NAME` | bundled libSFX example template to clone. default `Template`. |

```bash
retrotool libsfx scaffold demo
retrotool libsfx scaffold hello-world --template Hello
```

#### `retrotool libsfx build [<dir>]`

Assemble + link + post-process a libSFX project. Writes `<dir>/<name>.sfc`; with `--debug>=1` also writes `.sym` / `.map`; with `--debug=2` adds Mesen `.bp` breakpoints.

| flag | description |
|---|---|
| `dir` | project directory. default `.`. |
| `--debug {0,1,2}` | override the project's debug level. `0` = release, `1` = sym + map, `2` = sym + map + Mesen `.bp`. |
| `-o`, `--output OUT` | output ROM path. default: `<dir>/<name>.sfc`. |

```bash
# Build current dir at the project's configured debug level
retrotool libsfx build

# Build a specific dir at full debug; custom output path
retrotool libsfx build demo --debug 2 -o builds/demo-debug.sfc

# Release build (skip sym/map even if project.toml requests it)
retrotool libsfx build demo --debug 0
```

#### `retrotool libsfx info [<dir>]`

Print discovered sources + config without building. Useful to verify which `.s` / `.cfg` / asset files the project will pick up before kicking off a build.

```bash
retrotool libsfx info demo
```

#### `retrotool libsfx clean [<dir>]`

Remove `.build/` and the built `.sfc`. Keeps `.cache/` by default so cached asar/section artifacts survive.

| flag | description |
|---|---|
| `dir` | project directory. default `.`. |
| `--full` | also wipe `.cache/`. forces every cached section to rebuild on next `build`. |

```bash
retrotool libsfx clean              # remove .build + ROM, keep .cache
retrotool libsfx clean --full       # nuclear: also wipe BuildCache
```

---

### Exit codes

| code | meaning |
|---|---|
| `0` | success |
| `1` | uncaught error (printed as `error: <msg>` on stderr) |
| `2` | extract destination ambiguous (`--lang` not in `data_dirs_by_lang`, or no dest specified at all) |

---

### Driving retrotool from Python

Every `retrotool <cmd>` subcommand has a corresponding function in
`retrotool.build` that takes Python-friendly kwargs, prints the same
progress reporter + summary by default, and returns a structured result.
The CLI is a thin argparse layer on top — anything you can do via the
shell, you can do from a notebook or a build script.

```python
from retrotool.build import build_project

result = build_project("my-game/", diff="xdelta", jobs=4)
print(result.rom_path, result.checksum, result.duration_ms)
```

```python
# Quiet build (no progress, no summary) for CI / scripting:
result = build_project(
    "my-game/", no_progress=True, print_summary=False,
    only={"asar", "main_dialog:42"},        # set / list / CSV string all work
    defines={"version": "en"},
)
```

```python
# Programmatic bisection — equivalent to `--script-step-batch`:
from retrotool.build import iter_step_builds, load_spec
spec, spec_file = load_spec("my-game/")
section = next(s for s in spec.sections if s.from_datadef == "main_dialog")
for step, total, result, out in iter_step_builds(
    spec, section=section, source_root=spec_file.parent,
    out_path=spec_file.parent / "main_dialog.sfc",
    progress=16,                            # 16 blocks per step
):
    print(f"step {step}/{total}: {out.name} ({result.rom_size:,}b)")
```

| function | CLI equivalent |
|---|---|
| `build_project(path, **opts)` | `retrotool build <path>` |
| `extract_project(path, lang=, dest=, ...)` | `retrotool extract <path>` |
| `migrate_project(path, in_place=)` | `retrotool migrate <path>` |
| `iter_step_builds(spec, section=, ...)` | `--script-step-batch` (loop body) |
| `build_libsfx_project(dir, debug=, output=)` | `retrotool libsfx build` |
| `info_libsfx_project(dir)` | `retrotool libsfx info` |
| `clean_libsfx_project(dir, full=)` | `retrotool libsfx clean` |
| `scaffold_libsfx_project(dir, template=)` | `retrotool libsfx scaffold` |

Helpers used by the facade (also part of the public API):
`load_spec()`, `resolve_spec_path()`, `parse_defines()`,
`parse_csv_set()`, `parse_only_args()`, `parse_only_token()`,
`resolve_extract_dest()`, `resolve_jobs()`, `default_output_path()`,
`default_cache_dir()`, `make_overwrite_confirmer()`,
`workers_for_print()`. Pass any of `print_summary=False`,
`summary_stream=…`, `progress_stream=…`, `no_progress=True` to
silence or redirect the CLI-style output.

## Package Layout

```
retrotool/
├── core/            # primitives — ROM, addressing, binary, cache
├── project/         # TOML-based project + data-definition files
├── graphics/        # tile/palette/sprite/tilemap codecs
├── compression/     # LZSS (3 presets), RLE, registry, detector
├── script/          # .tbl codec, extractor, inserter, DTE, validator
├── debugger/        # Mesen2-Diz IPC client + automation
├── heuristics/      # pointer/text/gfx scanners + region mapper
├── asm/             # codegen, asar patcher, freespace, templates
├── extraction/      # Level/Entity/Behavior models + Pipeline
├── export/
│   ├── godot/       # .tscn / .tres / TileSet / SpriteFrames
│   ├── tiled/       # .tmx / .tsx
│   ├── cpp/         # struct headers
│   └── python/      # dataclass modules
├── ai/              # prompt templates + workflow steps
├── snes.py          # back-compat shim → core.address/pointer
└── script.py        # back-compat shim (via script/ package __init__)
```

Each submodule is importable on its own — `from retrotool.compression import LZSSCodec` does not
pull in the debugger, exporters, or any of graphics. Keeps CLI and GUI integrations cheap.

## What Each Module Is For

### `retrotool.core`
Address math and ROM loading. The only truly foundational module — everything else builds on it.

- `SFCAddress`, `SFCAddressType` — conversion between PC, LoROM1, LoROM2, HiROM, ExLoROM, ExHiROM.
- `SFCPointer` — 24-bit pointer with per-byte access and flexible constructors.
- `Rom` — file loader that strips SMC headers and scores candidate internal headers
  (LoROM/HiROM/ExHiROM) by checksum-complement XOR + map-mode sanity + printable title.
- `BuildCache` — SHA-256 keyed filesystem cache used by the asar patcher (and available
  to consumers that want to skip expensive regeneration steps).
- `binary.*` — `integer_or_hex`, `hex_fmt`, low/high/bank byte helpers, LE u8/u16/u24 read+write.

### `retrotool.project`
Project definition files. A project is a `project.toml` plus a tree of per-table `.toml`
data definitions that describe where things live in the ROM.

- `ProjectConfig` — parsed root document: rom info, build config, debugger config.
- `DataDef` — a single table/block: encoding, pointer table, data block, optional relocation,
  display constraints.
- `load_project(path)` / `load_datadef(path)` / `load_datadefs(project)`.
- `parse_snes_addr("$1B:8000")` / `parse_size("2M")` — literals used in the TOML files.

### `retrotool.graphics`
All pixel-level SNES formats.

- `Palette` — BGR555 ↔ RGB888 (bit-replicated), with transparent-index support.
- `Tile` / `decode_tile` / `encode_tile` — 1BPP-IL, 2BPP, 4BPP, 8BPP planar codec, with
  `flipped(h=, v=)` and grid compositor.
- `TilemapEntry` — 16-bit SNES tilemap word (tile 10b, palette 3b, priority, H-flip, V-flip).
- `project_tilemap(entries, src_cols, src_rows, *, tile_base=0, base_entry=0, dest_cols=32, dest_entries=1024, palette_remap=None, force_priority=False, skip_tiles=None)` —
  place a small tilemap into a larger sparse/windowed destination stream (for
  engines that DMA a fixed window of a BG tilemap). Offsets tile indices by
  `tile_base`, remaps palettes, leaves `skip_tiles` (e.g. the blank tile) as `$0000`.
- `SpriteFrame` / `render_frame` — compose frames from positioned 8×8 tiles; `pack_atlas`
  returns an `Atlas` with per-frame `AtlasEntry` origin metadata.

With the `[libsfx]` extra (0.9.0+):

- `png_to_tiles(png, bpp=4, mode="snes", no_flip=False, no_discard=False, palette=None, tile_width=8, tile_height=8)` — convert PNG to raw tile bytes.
- `png_to_palette(png, mode="snes", colors=16, palettes=8, color_zero=None, tile_width=8, tile_height=8)` — extract palette bytes (`color_zero="RRGGBB"` forces a fixed backdrop into index 0).
- `png_to_map(png, tiles, palette, bpp=4, mode="snes", tile_width=8, tile_height=8)` — build a tilemap that
  references previously-emitted tile + palette bins.
- `encode_png(png, *, bpp=4, colors=16, palettes=8, color_zero=None, no_flip=False, no_discard=False, fixed_palette=None, …)` →
  `EncodedGraphics(tiles, palette, entries, cols, rows)` — one SuperFamiconv pass yielding
  mutually-consistent tiles + palette + `list[TilemapEntry]`. `.subpalette_colors(n)` returns a
  subpalette's RGB for palette remapping. `fixed_palette=` (BGR555 bytes) skips palette extraction
  and packs against the given order. Pair with `project_tilemap` to reinsert edited
  word-art / UI graphics into an engine-specific tilemap.
- `png_palette_rgb(png)` — read an indexed PNG's PLTE as ordered `(r,g,b)` list.
- `grouped_palette_bytes(colors_rgb, *, subpalettes, colors_per)` — build a fixed BGR555 SNES
  palette from an ordered RGB list (`[shared idx0] + (colors_per-1)` per subpalette), preserving
  source order so re-encoded indices match a ROM's CGRAM.
- `sfc_run(args)` — raw pass-through to the bundled SuperFamiconv binary.
- `SFCNotFoundError` — raised when neither the bundled wheel nor a `superfamiconv`
  on `PATH` is available.

Not yet in v1.0: `font.py` (1BPP-IL VWF + 2BPP 16x16 glyph pipelines), `animation.py`.

### `retrotool.compression`
Unified codec framework. Parameterized LZSS covers all three variants shipped to date.

- `LZSSCodec(params)` — greedy longest-match compressor + table-driven decompressor.
  - Presets: `PARAMS_RBSHURA` (fill 0x00, `u16_le` header), `PARAMS_ZAMN`
    (fill 0x20, `u16_le_chain15` header), `PARAMS_LEGACY` (fill 0x00, no header).
  - `decompress_chain(data, offset, resolve_next)` — handles ZAMN's bit-15-chained blocks.
- `RLECodec(params, size=-1)` — ctrl-byte RLE (run_flag=0x80, length_mask=0x7F).
- `registry.get(name, params)` — schemes: `lzss`, `lzss-rbshura`, `lzss-zamn`,
  `lzss-legacy`, `rle`.
- `scan_lzss(data, presets, ...)` — brute-force candidate scanner with size/ratio filters.

Not yet in v1.0: Huffman, Nintendo LZ77.

### `retrotool.script`
Text extraction + insertion using `.tbl` files.

- `Table(path)` — loads a `.tbl` (`HH=char` lines, `**` variable substitution, `%%` double
  substitution). Provides `interpret_binary_data` (bytes → text, longest-match decode) and
  `encode_text` (text → bytes, with `[HH]` hex-literal escape).
- `extract_script(rom, datadef, table, address_type)` — reads the pointer table described
  in the `DataDef`, walks each string to its terminator, and returns `ScriptEntry[]` with
  both the raw bytes and the decoded text.
- `compile_script(texts, datadef, table, ...)` — compiles strings back to bytes and emits
  a pointer table targeting a relocation address.
- `find_digraphs` / `build_dte_table` / `apply_dte` / `savings_estimate` — DTE overflow
  helpers for tight text budgets.
- `round_trip(texts, table)` — validator that encode→decode→compares every string.

### `retrotool.debugger`
Mesen2-Diz IPC client. Transport is a newline-delimited JSON protocol over Unix domain
sockets at `/tmp/CoreFxPipe_{pipeName}` (Windows support currently stub).

- `MesenClient(pipe_name=...)` — low-level `call(command, **params)` plus wrappers:
  `read_memory`, `write_memory`, `get_cpu_state`, `pause`, `resume`, `step`,
  `add_breakpoint`, `remove_breakpoint`, `evaluate`, `take_screenshot`, `get_rom_info`,
  `get_status`.
- `derive_pipe_name("Rushing Beat Shura.sfc")` → `"Mesen2Diz_RushingBeatShurasfc"`.
- `paused(client)` — context manager that pauses emulation during a block.
- `run_until_breakpoint(client, addr, ...)` — install one-shot breakpoint, resume, poll.
- `MemoryRegion` + `watch(...)` — tick/diff loop over a ROM or RAM range.

Untested against a live Mesen process in v1.0 — wire format is implemented per the
documented protocol.

### `retrotool.heuristics`
Static ROM-scanning heuristics.

- `scan_pointer_tables(rom, entry_size=2, bank=?, valid_range=?, ...)` — slides across
  the ROM looking for runs of pointers whose targets all resolve into a valid range.
  Reports entry count, target-range bounds, and monotonic fraction.
- `scan_text(rom, min_length=16, ...)` — printable-byte runs separated by terminators.
- `scan_graphics(rom, bpp=4, window_tiles=32, ...)` — entropy + plane-pair correlation.
  Intended as a first-pass filter; confirm by rendering.
- `shannon_entropy(data)` — byte-distribution entropy (0–8).
- `Region` / `merge_regions` / `fill_gaps` — region-map builder that combines results
  from multiple scanners.

### `retrotool.asm`
Assembly patching + codegen.

- `AsmBuilder` — fluent builder: `.label().instr().db().comment().render()`.
- `AsarPatch(asm_file, includes=..., defines=...)` + `apply_patch(rom, patch, out, cache=)` —
  shells out to the `asar` binary, skips work when cache key matches.
- `BassPatch(asm_file, includes=..., defines=..., constants=..., strict=False)` +
  `apply_bass_patch(rom, patch, out, cache=, bass_cmd="bass")` — bass v18 (ARM9 fork)
  equivalent. Uses `bass -m <out>` modify-mode for asar-equivalent in-place patching.
  Same `BuildCache` integration; defines map to `-d`, constants to `-c`.
- `FreeSpace(regions)` — `.allocate(length, align, tag)` with coalescing and used/free
  bookkeeping; use it to lay out data/code placements before emitting `org` directives.
- `templates.hook_jsl / redirect_pointer_table / freespace_block / data_block` — string
  templates for common patterns.

### `retrotool.extraction`
Dataclass models for the things a disassembly typically produces. Pipeline is a
dependency-ordered runner so extraction can be staged.

- `Level` — layers + collision + triggers + spawns + palette zones.
- `EntityDef` / `EntityRegistry` — entity catalog.
- `Behavior` / `BehaviorState` — state-machine skeleton to annotate from disasm.
- `Pipeline` / `PipelineStage` — topologically-ordered runner over a shared context dict.

### `retrotool.export`
Text emitters for common downstream formats. Pure stdlib — no Godot/Tiled install required.

- `export.godot.GdScene` / `GdResource` — `.tscn` / `.tres` text generator with Godot's
  inline `ExtResource("…")`, `SubResource("…")`, `Vector2(x, y)` literal syntax.
- `export.godot.build_tileset` — `TileSetAtlasSource` resources + physics-layer specs.
- `export.godot.build_sprite_frames` — `SpriteFrames` resource from `Animation[]`.
- `export.godot.scaffold_project` — `project.godot` boilerplate.
- `export.tiled.build_tmx` — Tiled `.tmx` with CSV layer data + object group for triggers
  and spawns.
- `export.tiled.build_tsx` — Tiled `.tsx` tileset.
- `export.cpp.render_header` — namespaced header with `u8/u16/u24/u32 → uint*_t` types.
- `export.python.render_module` — `@dataclass` module.

### `retrotool.ai`
Prompt templates and dataclass shapes for **external** LLM-driven scripts. **No model calls
are made from this package** — it is a vocabulary, not a client. Designed so a downstream
script can do:

```python
from retrotool.ai import IDENTIFY_COMPRESSION, build_context
prompt = IDENTIFY_COMPRESSION.format(offset=0x10000, head=head_hex)
prelude = build_context(project, ...).to_prompt()
# ...send `prelude + prompt` to whichever LLM you wire up
```

- `prompts.*` — `str.format`-style templates: `IDENTIFY_COMPRESSION`, `LOCATE_TEXT_TABLE`,
  `DISCOVER_LEVEL_FORMAT`, `SUGGEST_ASAR_HOOK`.
- `workflows.*` — `WorkflowStep` dataclass + two canned step lists
  (`IDENTIFY_COMPRESSION_WORKFLOW`, `DISCOVER_TEXT_SYSTEM_WORKFLOW`) external orchestrators
  can iterate.
- `ipc_prompt.IpcPlan` / `IpcStep` — structured Mesen-IPC command sequences (`.to_json()`)
  that a `MesenClient` consumer can apply step-by-step.
- `context.build_context(project, ...)` — `ProjectConfig` → `ProjectContext` with
  `.to_prompt()` for the LLM prelude.

## MBXML builds

`retrotool.build` is an MBuild 1.29-compatible build pipeline plus extensions.
Every element on a build spec describes one piece of ROM data; the same spec
drives **build** (files → ROM) and **extract** (ROM → files). See the [CLI
Reference](#cli-reference) for the full flag set.

### MBuild 1.29 compatibility

All MBuild 1.29 elements parse (`<build>`, `<rep>`, `<ins>`, `<lzr>/<lzi>`,
`<rlr>/<rli>`, `<bpr>/<bpi>`, `<sbr>/<sbi>`, plus `pad`, `diff`, `revbyteloc`).
Legacy codec-matrix elements are **auto-migrated in memory** to the unified
form: `<lzr> → <bin codec= grow="replace">`, `<bpr> → <graphics>`,
`<sbr> → <script>`, etc. `retrotool migrate` writes the migration back to
disk (keeps a `.bak`). An `MBXMLDeprecationWarning` fires for each legacy tag.

Scoped-out vs. MBuild 1.29: Lunar Compress codecs, BM5/SFCW RLE, Windows
registry features. The first three land incrementally in 0.9.

### Retrotool extensions

- **Unified `<bin codec=>`** — `lzss-zamn`, `lzss-rbshura`, `lzss-legacy`,
  `rle`. `grow="replace|insert|fail"` controls size-change behavior.
- **`<graphics>`** — raw tile/palette data (`offset`, `bpp`, `count`,
  `encode="planar|packed"`, `codec=` via `retrotool.compression.registry`), OR
  **build-time PNG encode** when `file=` is a `.png` (or any `format=`/`map-offset=`
  is set): runs SuperFamiconv (`[libsfx]`) → tiles at `offset` (`bpp`, `color-zero`,
  `no-flip`, `tile-count` pad, `colors`, `palettes`). `palette-from-png="true"` packs
  against the indexed PNG's OWN palette order (PLTE laid out as `[shared idx0] +
  (colors-1)` per subpalette) so re-encoded pixel indices line up with a ROM's fixed
  CGRAM instead of being re-sorted. With `format="tilemap"`/`map-offset=` it also
  projects a tilemap (`tile-base`, `map-cols`, `map-entries`, `map-base-entry`,
  `priority`, `palette-anchors="P:RRGGBB,…"` mapping SuperFamiconv subpalettes → SNES
  palette #). Lets edited word-art round-trip back into a ROM straight from project.toml.
- **`<script>`** — text/binary round-trip via `.tbl`; `pointer-table=`,
  `table=`.
- **`<asar>`** — build-only; runs an asar patch. `defines=` / `includes=`
  are pipe-separated.
- **`<libsfx src=… debug= stack-size=>`** — build-only; assembles a libSFX
  project from scratch and installs the linked ROM as the working canvas.
  Subsequent `<rep>/<ins>/<bin>/<asar>` sections patch on top. When a
  `<libsfx>` is present, the `<build>` `original=` attr is optional.
- **`<project src=…>`** — nested spec applied against the parent ROM.
- **Variable interpolation** in any attr: `${env.FOO}`, `${build.path}`,
  `${datadef.main_dialog.pointers.address}`.
- **`if=` conditionals** (`==` / `!=` only) for multi-locale builds.
- **`<include src=…>`** — recursive splice, cycle-detected.
- **Per-section cache** — `retrotool.core.BuildCache` keyed on
  `(kind, attrs, input SHA-256)`. Disable with `--no-cache`.
- **Diff output** — pure-python IPS + xdelta3 subprocess (bundled via the
  optional `retrotool-xdelta` wheel, falls back to system `xdelta3`).

See `examples/mbxml/demo.mbxml` for a walkthrough.

### Project.toml front-end

The same pipeline accepts the retrotool-idiomatic `[build]` table in a
`project.toml`. Handlers, cache, extract are identical — pick whichever
front-end you prefer per project, or reference MBXML fragments from
`project.toml` via `<include>`.

## libSFX assembly projects

The `retrotool[libsfx]` extra bundles the Optiroc SNES toolchain (libSFX
runtime + ca65/ld65 + SuperFamiconv + SuperFamicheck + BRRtools + lz4 +
make\_breakpoints) as the companion wheel `retrotool-libsfx`. With it
installed, retrotool can scaffold, assemble, link, header-fix, and emit
Mesen breakpoints for a libSFX-layout project in pure Python — no user
subprocess calls, no `make`.

```bash
pip install 'retrotool[libsfx]'

# scaffold a fresh project from the bundled libSFX Template
retrotool libsfx scaffold demo

# build → demo/demo.sfc (+ .sym/.map/.bp when debug>=1)
retrotool libsfx build demo --debug 2

# inspect discovered sources + config
retrotool libsfx info demo
```

Or, from Python:

```python
from pathlib import Path
from retrotool.asm.libsfx import LibSFXProject, scaffold_libsfx_project

scaffold_libsfx_project(Path("demo"), template="Template")
project = LibSFXProject.discover(Path("demo"))
project.cfg.debug = 2
result = project.build()
print(result.rom, result.checksum, result.breakpoints)
```

`project.toml` drives the defaults:

```toml
[build.libsfx]
name = "demo"
src = "./game_src"
debug = 2
stack_size = 0x200
map_config = "Map_Mode21_2mbit.cfg"
```

### Driving libSFX from MBXML

A libSFX build can also be embedded as an MBXML section. Subsequent
`<rep>/<ins>/<bin>/<asar>` sections patch on top of the linked ROM:

```xml
<build name="demo">
  <libsfx src="./game_src" debug="2"/>
  <rep file="patch.bin" offset="10"/>
</build>
```

See [`examples/libsfx-hello/`](examples/libsfx-hello/) for a walkthrough,
and [`plans/libsfx-native-integration.md`](plans/libsfx-native-integration.md)
for the design rationale behind the A–J phase structure.

## Where this is going

retrotool is the **library floor** for a larger project: a desktop app that
sits above this codebase and makes the whole loop — reverse engineer, build,
patch, distribute — approachable without memorizing a CLI.

Roughly in priority order:

1. **Customizable project workbench.** A GUI that manages a project.toml /
   MBXML / libSFX project, wires up the debugger, runs extract/build with
   one click, and keeps per-game presets. Your scripts, your heuristics,
   your panels — retrotool is designed to be embedded, not owned.
2. **Patch manager.** Point at a ROM you own. The app computes SHA-256 /
   header checksum, looks up matching patches from a curated index (think
   `apt`/`npm` for ROM-hacks), shows compatibility + authorship +
   dependencies, and applies them with xdelta/IPS/asar/MBXML under the
   hood. Patches can be hosted anywhere — the index just holds references.
3. **Patch authoring.** The same workbench used to *consume* patches can
   publish them. Export a build pipeline as a reproducible patch bundle
   (MBXML + assets + asar hooks) with the checksum of the ROM it targets.
4. **Shared patch index (moonshot).** A community-maintained catalog
   mapping ROM hashes → patches hosted in GitHub (or anywhere), so
   "which translations and hacks exist for the cart I just dumped?"
   becomes a one-click question. Federated, not centralized — the app
   is the consumer, not the authority.

This is ambitious and intentionally scoped beyond 1.0. What *is* in scope
now: keep the library stable, keep the CLI honest, keep the pipeline
reproducible. Everything above builds on those three.

## Quick Tours

### Address math (v0.1 behavior still works)

```python
from retrotool import SFCAddress, SFCAddressType

addr = SFCAddress(0x5F800, SFCAddressType.PC)
print(addr.all())                 # show all applicable conversions
print(addr.hirom_address)         # '0xC5F800'
print(addr.lorom1_address)        # '0x0BF800'
```

### Load a ROM and read through the detected header

```python
from retrotool import Rom

rom = Rom.load("lm3.sfc")
print(rom.header.title, rom.header.mapping_name)    # e.g. 'LITTLE MASTER III' 'lorom'
print(f"{rom.header.rom_size_bytes:#x}")
some_bytes = rom.read_snes(0x81_8000, length=16)    # reads by SNES addr via detected mapping
```

### Define a project in TOML

`project.toml`:

```toml
data_dirs = ["scripts"]

[rom]
name = "Little Master III"
file = "lm3.sfc"
mapping = "lorom"
size = "2M"
expanded_size = "4M"

[rom.vectors]
reset = "$80:FFFE"
nmi = "$80:FFEA"

[build]
assembler = "asar"
output_dir = "out/"
cache_dir = ".cache/"

[debugger]
type = "mesen-diz"
```

`scripts/main_dialog.toml`:

```toml
[table]
name = "main-dialog"
type = "pointer"

[encoding]
table_file = "tables/eng.tbl"
terminator = 0x00

[pointers]
address = "$1B:8000"
count = 512
size = 2
bank_override = "$1B"

[data]
start = "$1B:8400"

[relocation]
target = "$C1:8000"
pointer_size = 3
```

```python
from retrotool import load_project
from retrotool.project import load_datadefs

proj = load_project("path/to/project.toml")
for d in load_datadefs(proj):
    print(d.name, hex(d.pointers.address), d.pointers.count)
```

### Extract a script from a ROM

```python
from pathlib import Path
from retrotool import Table, extract_script, load_project
from retrotool.project import load_datadefs
from retrotool.core import SFCAddressType

proj = load_project("examples/lm3")
rom = Path(proj.rom_path).read_bytes()
datadefs = load_datadefs(proj)

main = next(d for d in datadefs if d.name == "main-dialog")
tbl = Table(proj.root / main.encoding.table_file)

script = extract_script(rom, main, tbl, SFCAddressType.LOROM1)
for entry in script.entries[:5]:
    print(entry.id, entry.text)
```

### Compress / decompress LZSS

```python
from retrotool.compression import LZSSCodec, PARAMS_ZAMN, PARAMS_RBSHURA

codec = LZSSCodec(PARAMS_RBSHURA)
blob = b"Hello, World! " * 10
packed = codec.compress(blob).data
assert codec.decompress(packed).data == blob

# ZAMN chain handling:
zamn = LZSSCodec(PARAMS_ZAMN)
def resolve(data, ptr_off):
    # read the 4-byte LoROM pointer at ptr_off, return its PC offset in `data`
    ...
all_bytes = zamn.decompress_chain(rom_data, first_block_offset, resolve).data
```

### Decode tiles and a palette

```python
from retrotool.graphics import Palette, decode_tiles, tile_to_rgba

palette = Palette.from_bytes(rom_data, offset=0x14_2000, count=16)
tiles = decode_tiles(rom_data, offset=0x14_4000, count=64, bpp=4)
first_rgba = tile_to_rgba(tiles[0], palette)   # 8*8*4 bytes
```

### Convert a PNG to SNES tiles + palette + map (needs `[libsfx]` extra)

```python
from pathlib import Path
from retrotool.graphics import png_to_tiles, png_to_palette, png_to_map

png = "bg_layer.png"
tiles = png_to_tiles(png, bpp=4)
pal   = png_to_palette(png, colors=16, palettes=1)

Path("tiles.bin").write_bytes(tiles)
Path("pal.bin").write_bytes(pal)

tilemap = png_to_map(png, tiles="tiles.bin", palette="pal.bin", bpp=4)
Path("map.bin").write_bytes(tilemap)
```

### Drive the Mesen2-Diz debugger

```python
from retrotool.debugger import MesenClient, derive_pipe_name, paused

with MesenClient(derive_pipe_name("Rushing Beat Shura (J).sfc")) as mesen:
    with paused(mesen):
        regs = mesen.get_cpu_state()
        print(regs["pc"], regs["a"])
        data = mesen.read_memory("SnesWorkRam", 0x7E_1000, 128)

    bp = mesen.add_breakpoint(0xC0_8000, memory_type="SnesPrgRom", break_on="exec")
    mesen.resume()
    # ... poll get_status, then:
    mesen.remove_breakpoint(bp)
```

### Run heuristics on a ROM

```python
from retrotool.heuristics import (
    scan_pointer_tables, scan_text, scan_graphics,
    Region, merge_regions, fill_gaps,
)

rom = open("lm3.sfc", "rb").read()

ptrs = scan_pointer_tables(rom, entry_size=2, bank=0x1B,
                           valid_range=(0xD_8000, 0xE_8000), min_entries=16)
texts = scan_text(rom, min_length=16)
gfx = scan_graphics(rom, bpp=4, window_tiles=32)

regions = (
    [Region(p.offset, p.count * p.entry_size, "pointer_table", p.monotonic_fraction) for p in ptrs]
    + [Region(t.offset, t.length, "text", t.printable_ratio) for t in texts]
    + [Region(g.offset, g.length, "graphics", g.plane_correlation) for g in gfx]
)
classified = fill_gaps(merge_regions(regions, gap_tolerance=4), len(rom))
```

### Apply an asar patch with caching

```python
from pathlib import Path
from retrotool import BuildCache
from retrotool.asm import AsarPatch, apply_patch

cache = BuildCache(".cache")
result = apply_patch(
    rom=Path("lm3.sfc"),
    patch=AsarPatch(asm_file=Path("patches/main.asm"),
                    includes=[Path("patches/lib.asm")],
                    defines={"VERSION": "english"}),
    out=Path("out/lm3.patched.sfc"),
    cache=cache,
)
print("cache hit" if result.cache_hit else "rebuilt", result.ok)
```

### Apply a bass v18 patch with caching

bass (the ARM9 fork, [github.com/ARM9/bass](https://github.com/ARM9/bass)) is supported as an alternative assembler with the same `BuildCache` integration as asar. Use `kind="bass"` on a `[[rom.build.sections]]` entry, or call `apply_bass_patch` directly:

```python
from pathlib import Path
from retrotool import BuildCache
from retrotool.asm import BassPatch, apply_bass_patch

cache = BuildCache(".cache")
result = apply_bass_patch(
    rom=Path("lm3.sfc"),
    patch=BassPatch(asm_file=Path("patches/main.asm"),
                    defines={"VERSION": "english"},
                    constants={"COUNT": "0x40"},
                    strict=True),
    out=Path("out/lm3.patched.sfc"),
    cache=cache,
)
print("cache hit" if result.cache_hit else "rebuilt", result.ok)
```

Resolution order (caller-given path → bundled `retrotool-bass` wheel if installed → system `bass` on PATH) mirrors asar. The wrapper invokes bass in **modify mode** (`bass -m <out> ...`) so the assembler patches the working ROM in place — the asar-equivalent semantic. Defines pass through `-d`, constants through `-c`, and `strict=True` toggles `-strict` (warnings become errors).

### Assemble a ca65 source and overlay it (`<ca65>`)

`retrotool[libsfx]` bundles the cc65 toolchain (ca65/ld65/cc65/...). The `<ca65>` section assembles one or more `.s` sources, links them through ld65 against a `Map.cfg`, and overlays the linker output into the working ROM at `offset=`. Same shape as `<asar>` / `<bass>`, but a two-stage assemble→link pipeline instead of an in-place patcher.

```toml
# project.toml
[[rom.build.sections]]
kind = "ca65"
file = "patches/hook.s"           # or files = "a.s|b.s|c.s" for multi-source
config = "patches/hook.cfg"        # ld65 linker config (Map.cfg-style)
offset = 0x10000                   # PC where the linker output lands
length = 0x100                     # optional cap (pads short, errors on overflow)
pad-byte = 0xFF                    # fill byte when length > linker output
defines = "DEBUG=1|VER=en"         # ca65 -D pairs
includes = "patches/include"       # ca65 -I paths
cpu = "65816"                      # ca65 --cpu (default "65816")
debug = 2                          # 0..3; emits .sym/.map/.dbg next to ROM
```

A minimal `Map.cfg` for a 256-byte fixed-offset blob:

```
MEMORY {
    CODE: start=$0000, size=$100, type=ro, fill=yes, fillval=$00;
}
SEGMENTS {
    CODE:  load=CODE, type=ro, optional=no;
}
```

Equivalent Python:

```python
from pathlib import Path
from retrotool.asm import Ca65Assembler, Ld65Linker
from retrotool import BuildCache

cache = BuildCache(".cache")
asm = Ca65Assembler(
    include_dirs=[Path("patches/include")],
    defines={"DEBUG": "1", "VER": "en"},
    cpu="65816",
    cache=cache,
)
asm.assemble(Path("patches/hook.s"), Path("build/hook.o"))

linker = Ld65Linker(config=Path("patches/hook.cfg"), debug_level=2)
result = linker.link([Path("build/hook.o")], Path("build/hook.bin"))
# `result.rom` is the linker output; copy into the working ROM at offset.
```

`<ca65>` is in `_CACHEABLE_KINDS` by default — the linker output is a deterministic function of source bytes + `.include`/`.import` deps + config + defines + cpu + debug + ca65/ld65 versions. Disable per-section with `cache="0"` if you wire ld65 features that read external state.

### Emit Godot / Tiled assets

```python
from retrotool.export.godot import GdScene, GdNode, build_tileset, TileAtlas
from retrotool.export.tiled import build_tmx, build_tsx

scene = GdScene(
    root_name="Stage1", root_type="Node2D",
    nodes=[
        GdNode("TileMap", "TileMapLayer"),
        GdNode("Player", "CharacterBody2D", properties={"position": (16, 32)}),
    ],
)
open("stage1.tscn", "w").write(scene.render())

tileset = build_tileset([TileAtlas("res://tiles.png", (8, 8), 32, 256)])
open("tileset.tres", "w").write(tileset.render())

# level is a retrotool.extraction.Level
open("stage1.tmx", "w").write(build_tmx(level, tileset_source="tiles.tsx"))
open("tiles.tsx", "w").write(build_tsx("tiles", "tiles.png", 256, 256))
```

## Back-Compat

v0.1 import paths still work:

```python
from retrotool.snes import SFCAddress, SFCAddressType, SFCPointer, lorom_to_hirom
from retrotool.script import Table
```

These re-export from the new modules; existing scripts don't need updating to load under 0.8.

## Roadmap

See [`project-plan.md`](./project-plan.md) for the full 16-phase plan and per-phase status.
Short version:

- **0.8.x** — 12 library modules scaffolded; LZSS overlap bugfix + optional SuperFamiconv graphics pipeline.
- **0.9** — CLI (`retrotool …` subcommands) + example projects (lm3, rbshura, zamn, minimal) + pytest suite.
- **1.0** — (current) first stable release: the full library + CLI toolkit — address math, compression,
  script/table, build-time graphics encode, MBXML + libSFX project builds, asar/bass/ca65/xdelta
  integration, content-addressed build cache, and Mesen2 IPC.
- **Beyond 1.0** — GUI shell with customizable project explorer, game-adaptable script editor, graphics
  extractor, graphics editor, statistics polling the debugger, pointer-table inspector, asar build panel;
  and runtime-guided heuristics that combine static scans with live Mesen state (write-breakpoint pointer
  discovery, DMA-trace data localization, glyph-correlation text discovery, LZSS fingerprinting via
  ring-buffer detection).

## License

See [LICENSE](./LICENSE).

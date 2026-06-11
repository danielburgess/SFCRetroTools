# Changelog

## Unreleased

### `retrotool edit` — project-generic GUI script editor

The rbshura script editor now ships as a general tool: `retrotool edit [DIR]`
opens any retrotool translation project. Configuration is the new optional
`[editor]` / `[editor.control_codes]` / `[editor.preview]` /
`[editor.files."stem"]` tables in `project.toml`
(see :mod:`retrotool.script.editor_config` and the README CLI Reference);
with no configuration it follows `build_lang` + `<lang>_data_dir`, discovers
`tables/*_<lang>.tbl`, pairs a `jp` dir as the read-only reference column, and
runs text-only until a font is configured. Control-code semantics (newline /
page-break / terminator / opcode lengths / speaker-palette opcode) are fully
configurable; the F7-FF scheme is the default. Script files round-trip in
their original encoding (UTF-16 or UTF-8, BOM-sniffed), and overflow-mode
`<<<window …>>>` markers are preserved but excluded from preview/byte counts.
New `editor` extra installs the GUI deps: `pip install 'retrotool[editor]'`.

### `retrotool lang new` / `lang list` — stage a new translation language

Adopted from the rbshura project's `tools/setup_language.py` (written generic
from the start) into `retrotool.project.language`. `retrotool lang new
--from en --to fr` copies the script folder, adds `fr_data_dir`, sets
`build_lang`, re-suffixes `[rom] name`, and forks every language-bearing
asset the tomls point at (encoding tables, `bin` fonts, `graphics` art) while
keeping `asar`/`python` engine patches shared (`--fork-all` to fork those
too). Prints the full plan and confirms before writing; TOML edits are
textual so comments survive; re-runs degrade to repair passes. The port also
fixes a re-run bug where `[rom] name` gained a double suffix
(`mygame_fr_fr`). `retrotool lang list` shows declared languages and the
active `build_lang`.

### Error-handling unification (library code no longer prints / swallows)

One strategy across the library: diagnostics go to module loggers, user
input failures raise typed errors, and None-returns stay None-returns.

* `retrotool.core.address` — all 16 `print()` diagnostics (including two
  UNCONDITIONAL ones on invalid ExLoROM/ExHiROM addresses that polluted
  stdout during normal speculative conversions) now go to the module
  logger at DEBUG. Conversion semantics unchanged: unmappable input
  returns `None`, silently. Also fixes `lorom1_to_pc`/`lorom2_to_pc`
  defaulting `verbose=True` (every other converter defaults False).
* `retrotool.script.Table` — malformed .tbl lines now raise
  `TableParseError` listing every bad line (`file:line: text: error`)
  after the full file is scanned, instead of printing `ERROR: ...` and
  silently encoding wrong bytes with whatever loaded. `Table(path,
  strict=False)` restores skip-and-continue (each skip logged as a
  warning, counted in `.errors`). `export_csv` propagates I/O errors
  instead of printing "I/O error"; duplicate-encoding warnings go to the
  logger; `get_value`'s None contract is documented.
* `retrotool.asm.PatchResult.check()` — chainable raise-on-failure
  (`apply_patch(rom, p).check().output_rom`); raises the new `PatchError`
  carrying the assembler log and the failed result. The easy-to-miss
  `.ok` flag stays for callers that branch.
* `retrotool.build` — user-reachable `assert`s (stripped under `python
  -O`) replaced with `HandlerError`s: extract's split-write validation
  and the python-handler module import path.

### `docs/project-toml-reference.md` — full configuration schema

Every key the parsers and handlers actually read, in one reference:
top-level scalars (`data_dirs`, `<lang>_data_dir`, `build_lang`), `[rom]`,
`[rom.build]` (freespace, labels, include, order, section defaults),
`[[rom.build.sections]]` core keys plus the per-kind attribute tables
(graphics/asar/bass/ca65/python/project/libsfx), script placement and
overflow-strategy config, `[extract]`, `[mesen]`, `[editor]`, variable
interpolation, and the complete DataDef schema ([table]/[encoding]/
[pointers]/[data]/[relocation]/[section]/[[fields]]). The doc states the
addressing convention explicitly: every ROM location in build config is a
PC file offset. A sync test (`tests/test_config_reference_sync.py`) sweeps
the handler sources for consumed attribute keys and fails CI when code
grows a key the doc doesn't mention.

### Fixed: extract misread `pointers.offset` as a SNES address

`pointers.offset` / `pointer-table=` is a **PC file offset** — that was
always the build handlers' contract, but the extract pipeline tried a
SNES-address interpretation first. The two coincide numerically in HiROM's
mirror ranges (so HiROM projects like rbshura never noticed), but in LoROM
a pointer table at PC `0x8000-0xFFFF` was unconfigurable: PC-authored
values silently extracted from the wrong place (`$008000` misread as SNES
`$00:8000` = PC 0), and SNES-authored values crashed the build with
`NoneType >> int`. Extract now resolves through the same
`_resolve_pointer_table_pc` helper the build uses, which also
bounds-checks the table read and — when a value only makes sense as a SNES
address — raises an error naming the PC offset to author instead.

### `examples/translation-project/` — runnable quickstart

A complete translation project: annotated `project.toml` + DataDef, JP/EN
encoding tables, extracted JP script + EN translation, contributor setup
scripts (Linux/macOS/Windows, uv-based, adapted from the rbshura project),
and `tools/make_demo_rom.py` which synthesizes the source ROM so the full
extract → translate → build → `lang new` loop runs with no copyrighted ROM.
Guarded by a golden test (`tests/test_example_translation_project.py`) that
generates, builds, and re-extracts the example on every CI run.

## 0.9.3 — 2026-06-08

### `build_lang` — select the build's source-text language

A new top-level `build_lang = "xx"` scalar in `project.toml` chooses which
language the build sources its script text from: the build's source-text root
becomes `data_dirs_by_lang["xx"]` (i.e. the matching `xx_data_dir=` scalar)
instead of `en_data_dir`. This lets a project keep honestly-named per-language
roots (`en_data_dir = "data/en"`, `br_pt_data_dir = "data/br_pt"`) and build a
non-English language without repurposing `en_data_dir`. `en_data_dir` is now
also registered under `data_dirs_by_lang["en"]`, so `build_lang = "en"` and
`extract --lang en` resolve symmetrically. Absent `build_lang` → unchanged
(build reads `en_data_dir`). Unknown lang raises `SchemaError`.

`build_lang` also feeds the `${lang}` interpolation variable, so section
conditions like `if="${lang}==br_pt"` follow the build language automatically
instead of being pinned in a second place. Precedence: `-D lang=` (CLI) >
explicit `[rom.build] lang =` > `build_lang`. With no `build_lang`, `${lang}`
is unset unless declared, exactly as before.

## 0.9.2 — 2026-05-27

The full ROM-hacking toolkit (library + CLI) — address math, compression,
script/table, build-time graphics encode, MBXML and libSFX project builds,
asar/bass/ca65/xdelta integration, content-addressed build cache, and Mesen2
IPC. Includes the changes below plus the cumulative 0.9.x work.

### Tilemap priority-bit isolation (`render_tilemap(..., transparent_if_priority=True)`)

`render_tilemap` now honors the previously-stub `transparent_if_priority` flag.
When True, entries whose SNES tilemap priority bit is set are skipped, leaving
their 8×8 region fully transparent (RGBA 0,0,0,0) in the output buffer. Use it
to extract the non-priority BG layer for export or preview without disturbing
the per-pixel palette alpha on rendered (non-priority) cells. Default is
False — every entry renders regardless of the priority bit, as before.

### Overflow strategy: configurable `undersized` behavior

`InlineRedirectStrategy` now exposes an `undersized` option for the case where
a slot is smaller than the redirect stub (`marker + pointer`):

```toml
[scripts.main.overflow]
strategy = "inline-redirect"
undersized = "preserve"      # or "error" (default — fail loud)
```

`"error"` (default) raises a clear `OverflowError` so the budget mistake isn't
silent. `"preserve"` keeps the original source ROM bytes for that entry
untouched (the lm3 case: entries reached only via external pins, where
overwriting them would break cross-entry redirects). The `marker` is already
configurable per project — error messages no longer hard-code the FFC0 byte
sequence.

### Default output directory (`[rom.build].output_dir`)

`[rom.build]` now accepts `output_dir` (aliases: `out-dir`, `out_dir`). When set
and no explicit `output=` is passed to `build_project()` (or `-o` to
`retrotool build`), the built ROM lands at `<output_dir>/<rom.name>.sfc`,
resolved relative to the spec file (absolute paths honored). The directory is
created if missing. Unset → output next to the spec file, as before. An
explicit `output=`/`-o` always wins.

```toml
[rom]
name = "mygame_en"
[rom.build]
output_dir = "out"      # → out/mygame_en.sfc
```

### Build-time PNG graphics encode (`<graphics>` / `kind="graphics"`)

`<graphics>` sections now accept a `.png` `file=` (or any `format=`/`map-offset=`
attr) and encode it through the bundled SuperFamiconv (`[libsfx]`) at build time,
so edited word-art / UI graphics round-trip back into a ROM straight from
`project.toml` — no pre-baked bin step.

```toml
[[rom.build.sections]]
kind = "graphics"
file = "art/title.png"
bpp = 2
offset = "$210000"          # tiles destination (file offset)
tile-count = 36             # pad tiles to N (e.g. a fixed DMA budget)
color-zero = "FF00FF"       # force backdrop colour into palette index 0
# optional tilemap projection:
map-offset = "$210900"
tile-base = "$E0"           # added to tile indices (VRAM slot the DMA targets)
map-cols = 32
map-entries = 128
map-base-entry = 2          # destination entry of the plate's top-left cell
priority = true
palette-anchors = "4:E7E700,5:FF5A08"  # subpalette (by anchor colour) -> SNES palette #
```

`palette-from-png="true"` packs against the indexed PNG's own palette order
(PLTE = `[shared idx0] + (colors-1)` per subpalette) so re-encoded pixel indices
line up with a ROM's fixed CGRAM rather than being re-sorted by SuperFamiconv.

New `retrotool.graphics` API backing it: `encode_png(...) -> EncodedGraphics`
(one SuperFamiconv pass → consistent tiles + palette + `list[TilemapEntry]`;
`fixed_palette=` packs against a supplied BGR555 order), `project_tilemap(...)`
(place a small plate into a larger sparse/windowed tilemap with tile-base offset,
palette remap, and blank-tile skip), `png_palette_rgb()` / `grouped_palette_bytes()`
(read + group an indexed PNG's palette), and `color_zero`/`colors`/`palettes`/
`tile_width`/`tile_height` flags on the `png_to_tiles`/`png_to_palette`/`png_to_map` wrappers.

Note for handler authors: build handlers must **return every `WriteRange`** they
write — the driver reconstructs the output ROM from the returned ranges, so a
side-mutation outside the return value is dropped. `_handle_graphics_png` returns
`[tiles_range, map_range]`.

### Mesen2 SRAM auto-sync

Opt-in post-build hook that clones the source ROM's `.srm` to match the
output ROM's filename, so a Mesen2 save state started on the base ROM
carries over to every patched build without a manual copy step. Driven
by a `[mesen]` table in `project.toml`:

```toml
[mesen]
sync-sram = true
saves-dir = "~/.config/Mesen2/Saves"   # optional; default on Linux
archive-overwritten = true             # optional; default true
```

After writing the output ROM the driver copies
`<saves-dir>/<source_stem>.srm` → `<saves-dir>/<out_stem>.srm`. The
source SRM is never written to: if source and output share a stem (so
the two paths resolve to the same file) the helper raises
`SramSyncError` instead of clobbering. Silent no-op when the source SRM
doesn't exist.

**Archive-before-overwrite**: when `archive-overwritten=true` (default)
and the destination SRM exists with content different from both the
source and every entry already in the archive, the existing destination
is appended to a single persistent archive
`<saves>/<dst_stem>_archive.tar.gz`. Entries inside are organized as
`<YYYY-MM-DD>/<dst_stem>_<HHMMSS>.srm`, so long-term playtest history
accumulates under date folders. Bytes already in the archive are not
re-archived (content-addressed dedupe); same-second collisions get a
numeric suffix.

Defaults: sync off, archive on (archive only fires when sync fires).
`BuildSpec.sync_sram` / `mesen_saves_dir` / `archive_sram` carry the
configuration; `retrotool.debugger.mesen_saves.sync_sram()` is also
exposed for direct callers and returns an `SramSyncResult` with
`copied` / `archived` paths populated as applicable.

### BuildCache: opt-in ASAR caching via `cache="1"`

Per-section cache override for kinds that are uncached by default. A
section may set `cache = true` (or `"1"` / `"yes"`) in `[[rom.build.sections]]`
/ DataDef `[section]` / MBXML attrs to opt into caching; `cache = false`
forces off on otherwise-cacheable kinds.

For `<asar>` with `cache="1"` the handler shifts to **diff mode**: only
byte ranges asar actually changed are recorded, so cache replay applies
an overlay independent of prior-section output. The default path still
returns a whole-ROM `WriteRange` (historical behavior).

Cache-key coverage for opted-in asar sections:

- Entry `.asm` + transitive `incsrc` / `include` / `incbin` (see
  `retrotool.build.asar_deps.scan_deps`). Cycles guarded; missing refs
  silently skipped (the assembler surfaces those as real errors).
- `includes=` / `defines=` / `allow-shrink=` attrs hashed explicitly so
  a trailing attr shift doesn't collide or falsely invalidate.
- Line-oriented comment strip (`//` / `;` / `/* */`) before scanning.
  Literal paths only — dynamic includes built from `!define` expansions
  or macros are NOT followed; users with such patches should leave
  `cache` unset.

`_CACHE_VERSION` bumped 4 → 5; pre-v5 entries silently invalidated.

Measured on LM3 after flipping `cache = true` on `debug-mode`:
cache hits rose from 24/27 → 25/27; full second build 35s → 137ms;
touching the `.asm` (mtime only) stays 25/27; editing content drops to
24/27 and bounces back when reverted. Output ROM is byte-identical to
a freshly-built one.

### BuildCache: coverage expansion + key fix

Two related fixes to per-section caching:

- `_CACHEABLE_KINDS` now includes `FIXED_RECORDS` and `WINDOWED_SCRIPT`.
  Both have deterministic, single-handler writes and benefit from cache
  replay. `ASAR` / `PROJECT` remain uncached pending a proper incsrc /
  incbin dependency scanner (editing an included `.asm` would otherwise
  replay stale output).
- `_section_cache_key` now hashes all typed Section fields that affect
  handler output (`offset`, `stride`, `count`, `fields`, `pointer_table`,
  `pointer_size`, `terminator`, `word_wrap`, `overflow`, `placement`,
  `textbuf_limit`, `codec`, `fallback_table`, ...). Previously only
  `size` + `grow` + `attrs` + `files` + `table` were hashed — fine for
  inline sections that land all config in `attrs`, but DataDef-derived
  sections hang their configuration off typed fields (attrs is empty),
  which meant two datadefs sharing one input file could collide on key.

`_CACHE_VERSION` bumped 3 → 4; pre-v4 cache entries are silently
invalidated on first post-upgrade build.

Measured on LM3: cache-hit count rose from 20/27 → 24/27, and a
second-pass build dropped 6.1s → 142ms.

### fixed-records: structured text-packing

`handle_fixed_records` now accepts a `<<$HEX:idx.label>>`-delimited text
script as its source (UTF-8 or UTF-16 LE BOM). The handler packs per-field
bytes using a `fields = [...]` schema (`{label, start, len, fill}`) into a
`stride * count` record buffer and patches the target region. Non-field
bytes inside each record stride are preserved from the working ROM, so
stats / unmapped sub-fields stay intact alongside the translated text.

Dispatch is automatic: a source starting with a UTF-16 BOM or containing
`<<$` in the first 4KB is treated as a script; otherwise the file is read
as a pre-packed `stride * count` binary (the existing asset-pipeline
behavior). Pre-packed flows keep working unchanged.

TOML/MBXML attr surface: `Section.fields` is populated from the DataDef's
top-level `[[fields]]` table or equivalent inline attr. The existing
`stride` / `count` already flow from `block_len` / `entries` (or
`[pointers]`).

Test coverage: `tests/build/test_fixed_records.py` adds 7 cases for the
text path (basic pack, truncation, unknown-label error, out-of-range
index, multi-field records, UTF-16 BOM, missing-schema error).

### Build filter (`--only` / `--skip`)

Expanded `_section_kinds_filter` to match more identifiers per section:

- `section.kind` (e.g. `asar`, `bin`, `script`, `fixed-records`).
- `section.from_datadef` — DataDef name for `[section]`-backed sections.
- `section.attrs["name"]` or `section.attrs["alias"]` on inline
  `[[rom.build.sections]]` entries.
- Positional: `sections[N]` (matches `section.source` suffix) and
  `section[N]` (singular alias for the same index). Either spelling works.

**`alias=` vs `name=` semantics on inline sections:**

| Key | Collides with | Use for |
|-----|---------------|---------|
| `alias` | nothing | grouping multiple sections under one filter tag |
| `name` | another `name=` or a DataDef of the same name | unique identity |

`alias` intentionally has no uniqueness constraint — two inline sections may
share the same `alias` (or share one with a DataDef name) so a single
`--only <alias>` selects every tagged section. `name=` is still used by
`merge_sections` as the section's unique merge key; duplicates raise
`SchemaError`. Prefer `alias=` for tagging and `name=` only when you need an
identity that also survives `[rom.build].order`.

Example: in LM3, three inline sections share `alias = "title"` so
`--only title` rebuilds the title dir-streams bin + chunks bin + chunk
relocate asar patch as one unit.

### word-wrap: pad mode

`[word-wrap]` now accepts `wrap-mode = "pad"` (default: `"newline"`) plus
`fill-char = " "` (default single space). In pad mode, each non-final wrapped
line is padded to exactly `line-width` columns with `fill_char` and **no
newline token** is emitted — intended for text engines that hardware-wrap
on a fixed column and render newline tokens as visible artifacts (e.g.
unit-info panels). Bracket/brace tokens are zero-col and don't consume pad
budget. `fill-char` must be a single literal character in pad mode.

Config may be placed at project level (`[rom.build.section.word-wrap]`) or
per-table (`[word_wrap]` inside a DataDef's TOML).

## 0.9.0 — 2026-04-15

**libSFX native integration** — the full Optiroc SNES toolchain now ships as
a companion `retrotool-libsfx` wheel (ca65/ld65 + libSFX runtime +
SuperFamiconv + SuperFamicheck + BRRtools + lz4 + make_breakpoints), exposed
via the `retrotool[libsfx]` extra. retrotool can scaffold, assemble, link,
header-fix, compress, and emit Mesen breakpoints for a libSFX project end-to-
end in pure Python — zero user-facing subprocess calls.

- `retrotool._toolchain` — centralized binary resolution (bundled →
  `$PATH` → `ToolchainError`). All subsystems dispatch through it.
- `retrotool.asm.ca65` — `Ca65Assembler`, `Ld65Linker`, dataclass results
  (`AsmResult`, `LinkResult`), content-hash-keyed object caching via
  `retrotool.core.BuildCache`.
- `retrotool.asm.libsfx` — `LibSFXProject.discover/assemble/link/build`
  mirrors `libSFX.make`, reads `[build.libsfx]` from `project.toml`,
  auto-globs `.s/.s700/.sgs` sources. `scaffold_libsfx_project` copies
  the bundled Template.
- `retrotool.rom.header` — `fix_rom_header` (pure-python wrapper over
  `superfamicheck`).
- `retrotool.audio.brr` — `encode_brr` / `decode_brr`.
- `retrotool.compression.lz4_cli` — temp-pipe wrapper over the bundled
  `lz4` binary (pure-python replacement tracked for 0.9.x).
- `retrotool.debugger.breakpoints` — `make_mesen_breakpoints` (pure
  Python; no bash dep on Windows).
- `retrotool libsfx {scaffold,build,info,clean}` CLI.
- MBXML `<libsfx src=… debug= stack-size=>` element: assembles a libSFX
  project as the working ROM canvas, so subsequent
  `<rep>/<ins>/<bin>/<asar>` sections patch on top. `<build original=>`
  becomes optional when a `<libsfx>` is present.
- `examples/libsfx-hello/` demonstrates the MBXML + libSFX flow.

Migration from 0.8.x: `retrotool-superfamiconv` is retired. Install
`retrotool[libsfx]` for the unified toolchain wheel.

## 0.8.2 — 2026-04-14

- **LZSS compressor**: fixed ring-buffer-overlap bug. Compressor now simulates
  the decoder's concurrent read/write during back-reference copies, so repeating
  patterns like `ABABAB…` encode as a single reference instead of literals. Any
  ring-buffer position in `[wpos, wpos+mlen)` during match extension is treated
  as the already-written source byte, matching decoder behavior. Round-trip
  verified; no format change.

(The `retrotool.graphics.superfamiconv` wrapper and the `retrotool-libsfx`
companion package are present in the tree but not yet published. The full
Optiroc SNES toolchain — libSFX runtime + ca65 + SuperFamiconv + SuperFamicheck
+ BRRtools + lz4 + make\_breakpoints — ships as a single `retrotool-libsfx`
wheel in 0.9.0, exposed via the `retrotool[libsfx]` extra. retrotool will
support either ca65 (via libsfx) or asar (existing) as the assembler.)

## 0.8.1 — 2026-04-13

Packaging metadata pass. No code changes.

- Expanded `description` to list toolkit scope (address math, compression, script, debugger IPC, asar, Godot/Tiled export).
- Added `readme = "README.md"` so PyPI renders the long description.
- Added `keywords` for PyPI discovery (snes, sfc, rom-hacking, asar, mesen, lzss, godot, tiled, …).
- Added `classifiers`: Beta status, Python 3.12/3.13, Public Domain license, Disassemblers / Games-Entertainment topics.
- Added `[tool.setuptools.packages.find]` — include `retrotool*`, exclude `tests*`/`examples*`. Fixes stray non-package dirs ending up in the wheel.
- Added `Repository` and `Issues` URLs alongside `Homepage`.

## 0.8.0

Version bump from 0.1.x line. Consolidation milestone — structure in place for v2 unified toolkit; much functionality still to land.

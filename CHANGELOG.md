# Changelog

## 1.0.0 — 2026-05-26

First stable release: the full ROM-hacking toolkit (library + CLI) — address
math, compression, script/table, build-time graphics encode, MBXML and libSFX
project builds, asar/bass/ca65/xdelta integration, content-addressed build
cache, and Mesen2 IPC. Includes the changes below plus the cumulative 0.9.x
work.

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

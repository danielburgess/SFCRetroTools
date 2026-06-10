# project.toml & DataDef reference

The complete configuration schema for retrotool projects: every key the
parsers and handlers actually read, with types, defaults, and semantics.
Derived from the source of truth (`retrotool/build/front_ends/project_toml.py`,
`retrotool/build/handlers.py`, `retrotool/build/overflow.py`,
`retrotool/project/datadef.py`, `retrotool/project/loader.py`); a sync test
(`tests/test_config_reference_sync.py`) fails CI when a handler grows an
attribute this document doesn't mention.

A project consists of:

- **`project.toml`** — the project: ROM identity, languages, build pipeline,
  inline sections, extract/editor/tooling config. *(this document)*
- **DataDef tomls** — one per game-data block (a dialog script, a name table),
  discovered from the folders listed in `data_dirs`. *(second half of this
  document)*
- **`.tbl` files** — byte ↔ character tables (`41=A`, `80=こ`; `@ctrl_prefix`
  directives for multi-byte control codes).
- **Script dumps** — UTF-16 text with `<<$ADDR:idx[$PTR]>>` headers, produced
  by `retrotool extract`, consumed by the build and `retrotool edit`.

For a worked example of all four, see
[`examples/translation-project/`](../examples/translation-project/).

## Addressing convention

**Every ROM location in build configuration is a PC file offset** — a byte
position in the ROM file (after SMC-header stripping), *not* a SNES bus
address. That includes `[[rom.build.sections]] offset=` / `pointer-table=`,
`[rom.build].freespace` ranges, and DataDef `[pointers].offset` /
`[data].offset` / `[data].end`. A value that only makes sense as a SNES
address is rejected with an error naming the PC offset to author instead.
(In HiROM the two encodings coincide numerically for much of the file, which
makes configs look SNES-addressed — they aren't.)

Numeric values accept: TOML integers, `"0x1A000"`, `"$1A000"`,
`"$01:A000"` (colon stripped — MBuild raw-hex convention), bare hex strings,
and decimal strings. Underscores are allowed and stripped.

Booleans authored as strings accept `"1"/"true"/"yes"/"on"` and
`"0"/"false"/"no"/"off"` (case-insensitive).

---

## Top-level scalars

TOML requires scalars to appear before the first `[table]` header.

| key | type | default | description |
|---|---|---|---|
| `data_dirs` | list[str] | `[]` | Folders scanned for DataDef tomls (each `*.toml` found becomes a data definition; those with a `[section]` join the build). |
| `<lang>_data_dir` | str | — | Per-language script root, e.g. `en_data_dir = "data/en"`, `jp_data_dir = "data/jp"`. Drives `extract --lang X`, DataDef file auto-defaults, `build_lang`, `retrotool lang`, and `retrotool edit`. |
| `build_lang` | str | `"en"` behavior | Which language's `<lang>_data_dir` the build sources script text from. Also seeds the `${lang}` interpolation variable. Set by `retrotool lang new`. |

## `[rom]`

| key | type | default | description |
|---|---|---|---|
| `name` | str | required | Project/ROM name. Built ROM lands at `<output_dir>/<name>.sfc`; seeds `${name}`. |
| `file` | str | required | Source ROM path (keep it gitignored — never distribute). |
| `mapping` | str | — | Address mapping: `lorom`, `lorom1`, `lorom2`, `hirom`, `exlorom`, `exhirom`, `sa1`. Drives all SNES↔PC math (pointer encoding, bank derivation). |
| `size` | int/str | required* | ROM size: `"4M"`, `"512K"`, `0x80000`, `524288`. *Required by the ProjectConfig loader — i.e. whenever `data_dirs` DataDefs are used; the inline-sections-only path tolerates its absence. |

`[rom.vectors]`, `[rom.sram]`, `[rom.hardware]` sub-tables are read by the
ProjectConfig loader (`retrotool.project.load_project`) for header/vector
tooling, not by the build front-end.

## `[rom.build]`

| key | type | default | description |
|---|---|---|---|
| `output_dir` (aliases `out-dir`, `out_dir`) | str | next to spec | Default output directory; explicit `-o` always wins. Created if missing. |
| `version` | str | — | Seeds `${version}` (for `if="${version}==en"` conditions). |
| `revision` | str | — | Seeds `${revision}`. |
| `revbyteloc` | int | — | PC offset where a revision byte is written. |
| `lang` | str | `build_lang` | Per-build `${lang}` override. Precedence: `-D lang=` > `[rom.build].lang` > top-level `build_lang`. |
| `path` | str | spec dir | Base for relative file references in sections. |
| `pad` | bool | false | Pad the output ROM to the declared `[rom].size`. |
| `pad-byte` / `pad_byte` | int | 0x00 | Byte used for expansion/tail padding. |
| `diff` | str | — | Emit a patch alongside the ROM: `ips`, `xdelta` (CLI `--diff both` builds both). |
| `jobs` | int/"auto" | 1 | Gather-phase worker threads. `0`/`"auto"` = `os.cpu_count()`. CLI `-j` wins. |
| `include` | list[str] | `[]` | Sibling TOML fragments to merge (`[rom.build]` only; parent wins on clash; cycles rejected). |
| `order` | list[str] | offset sort | Explicit pipeline order by section/DataDef name; unlisted sections follow, sorted by offset. |
| `freespace` | list[[lo, hi]] | `[]` | Half-open PC ranges `[lo, hi)` the relocate/overflow allocators may write into, walked in declaration order. |

**`[[rom.build.labels]]`** — global label registry: each entry has `name`
(str) and `at` (PC offset). Referenced from script bodies as `[FFC0@@name]`
and exported by sections via `export-label=`.

**`[rom.build.section.placement]` / `[rom.build.section.overflow]`** —
project-wide defaults inherited by every *DataDef-derived* section that
doesn't set its own (inline sections are unaffected). Most projects set
`[rom.build.section.placement] mode = "relocate"` once here.

## `[[rom.build.sections]]` — inline sections

Each entry is one build step. Recognized keys are validated; anything else
is preserved in `section.attrs` for the handler (see per-kind tables below).

| key | type | default | description |
|---|---|---|---|
| `kind` | str | required | Handler: `bin`, `rep`, `ins`, `asar`, `bass`, `ca65`, `graphics`, `script`, `windowed-script`, `fixed-records`, `project`, `libsfx`, `python`, plus MBuild legacy `lzr`/`lzi`/`rlr`/`rli`/`bpr`/`bpi`/`sbr`/`sbi`. |
| `file` (alias `src`) | str/list | `[]` | Source file(s), relative to `[rom.build].path`. |
| `offset` | int | — | PC write target. |
| `size` | int | — | Byte length (bounds checks; extract read length). |
| `codec` | str | — | Compression codec for `bin`: `lzss`, `lzss-rbshura`, `lzss-zamn`, `lzss-legacy`, `rle`, … (see `retrotool.compression.registry`). |
| `grow` | str | `replace` | `replace` (overwrite in place), `insert` (allow the write to extend the ROM), `fail`. |
| `count` | int | — | Entry count (script pointer tables, fixed-records). |
| `stride` | int | — | Bytes per record (`fixed-records`). |
| `pointer-table` | int | — | PC offset of the script pointer table. |
| `pointer-size` | int | 2 | Pointer width: 2 (within-bank, bank implied by the table's bank) or 3 (each entry carries its bank). |
| `terminator` | int | 0x00 | End-of-entry byte for script/fixed-records. |
| `table` | str | — | `.tbl` encoding table for script kinds. |
| `fallback-table` | str | — | Secondary `.tbl` consulted when the primary has no match. |
| `dedupe` | bool | false | Identical entries share one placement. |
| `textbuf-limit` | int | — | Per-entry encoded-byte budget (with redirect-chain accounting). |
| `pad-to` | int | — | Pad output to a boundary/size. |
| `cache` | bool/unset | per-kind | Per-section BuildCache override. `ca65` caches by default; `asar`/`bass` are opt-in (`cache = true` uses diff-mode writes — only safe if the patch doesn't read bytes other sections produce). |
| `if` | str | — | Condition: `${var}==literal` or `${var}!=literal`; section skipped when false. |
| `name` / `alias` | str | — | Identifier for `--only`/`--skip`, `order`, progress output. |
| `export-label` | str | — | After the section runs, register its first write offset as a global label (any kind). |
| `[word-wrap]` | table | — | Script wrapping: `line-width`, `max-lines`, `entries`, `newline`, `wrap-mode`/`wrap_mode`, `fill-char`/`fill_char`. |
| `[placement]` | table | — | Script placement; see below. |
| `[overflow]` | table | — | Script overflow strategy; see below. |

### Per-kind attributes

Handler-specific keys (stored via `section.attrs`). Numeric graphics attrs
parse hex with `$`/`0x` prefix, else decimal.

**`kind = "graphics"`** — PNG → SNES tiles (+ optional tilemap) at build time:

| attr | type | default | description |
|---|---|---|---|
| `bpp` | int | 4 | Bits per pixel: 2, 4, 8. |
| `colors` | int | 4 (2bpp) / 16 | Colors per subpalette. |
| `palettes` | int | 8 | Subpalette count. |
| `no-flip` | bool | false | Disable tile flip dedup. |
| `color-zero` | str | — | `RRGGBB` forced transparent color. |
| `palette-from-png` | bool | false | Keep the PNG's indexed palette order. |
| `palette-anchors` | str | — | `P:RRGGBB,…` — pin subpalettes to SNES palette slots by anchor color. |
| `format` | str | `tiles` | `tiles` or `tilemap` (auto-`tilemap` when `map-offset` set). |
| `map-offset` | int | — | PC offset for the 16-bit tilemap entries. |
| `map-cols` | int | 32 | Tilemap stride (cells per row). |
| `map-entries` | int | 1024 | Total tilemap entries. |
| `map-base-entry` | int | 0 | Entry index of the top-left cell. |
| `tile-base` | int | 0 | Added to tile indices (VRAM slot base). |
| `tile-count` | int | — | Pad/cap the tile count (error if exceeded). |
| `priority` | bool | false | Set the priority bit on tilemap entries. |
| `encoder` | str | — | Custom encoder `path/mod.py:func` or `pkg.mod:func` — replaces the built-in pipeline. |

**`kind = "asar"`**:

| attr | type | default | description |
|---|---|---|---|
| `includes` | str | — | Pipe-separated `-I` include dirs (`"inc|patches"`). |
| `defines` | str | — | Pipe-separated `NAME=VALUE` defines. |
| `allow-shrink` | bool | false | Permit asar to shrink the ROM. |

**`kind = "bass"`**:

| attr | type | default | description |
|---|---|---|---|
| `includes` / `defines` | str | — | As asar (`-d` string defines). |
| `constants` | str | — | Pipe-separated `-c` numeric constants. |
| `strict` | bool | false | Pass `-strict`. |
| `bass-cmd` | str | bundled | Explicit bass binary path. |
| `allow-shrink` | bool | false | Permit ROM shrink. |

**`kind = "ca65"`** (assemble + link via ld65; cached by default):

| attr | type | default | description |
|---|---|---|---|
| `config` | str | required | ld65 linker config (must emit a flat binary). |
| `files` | str | — | Pipe-separated additional sources. |
| `includes` / `defines` | str | — | `-I` dirs / `-D` defines (pipe-separated). |
| `lib-paths` / `cfg-paths` | str | — | ld65 `--lib-path` / `--cfg-path` entries. |
| `cpu` | str | `65816` | ca65 `--cpu`. |
| `debug` | int | 0 | 0–3; emits `.sym`/`.map`/`.dbg` artifacts. |
| `sym` | str | — | Copy the `.sym` to this project-relative path. |
| `length` | int | — | Cap output bytes (`pad-byte` fills; over-length errors unless `allow-truncate`). |
| `pad-byte` | int | 0x00 | Fill byte for `length`. |
| `allow-truncate` | bool | false | Silently truncate over-`length` output. |

**`kind = "python"`** — arbitrary build step,
`fn(rom, section, root, ctx) -> WriteRange | list[WriteRange] | None`:

| attr | type | default | description |
|---|---|---|---|
| `module` | str | — | Import path (`pkg.mod`); or use `file=` for a script path. |
| `func` | str | `build` | Callable name. |

**`kind = "project"`** — nested spec build: `src` (or `file`) names the
nested `.mbxml`/`.toml`; the parent ROM is the canvas.

**`kind = "libsfx"`** — libSFX project build: `src` (or `file`) is the
project root; `debug` (0–3) and `stack-size` override the libSFX config.

### Script sections: placement, overflow, windows

A script section needs `placement.mode` (set per-section or via the
project-wide `[rom.build.section.placement]` default):

- **`mode = "relocate"`** — re-encode every entry, pack the data after the
  pointer table (and into `freespace` for 24-bit pointers), rewrite the
  pointer table. The original string bytes are left untouched.
- **`mode = "overflow"`** — keep the pointer table; entries are patched
  in place inside `<<<window N: $LO-$HI>>>` regions declared in the script
  file, with oversize entries handled by the overflow strategy.

`[overflow]` strategy config (consumed by `retrotool.build.overflow`):

| key | type | default | description |
|---|---|---|---|
| `strategy` | str | required | `fail`, `truncate`, or `inline-redirect` (or a registered custom). |
| `marker` | bytes/hex | `FF C0` | Inline-redirect marker bytes. |
| `pointer-size` | int | 3 | Redirect pointer width. |
| `pointer-encoder` | str | `snes-lorom1-24le` | Registered pointer encoder for redirect targets. |
| `return-pointer-encoder` | str | — | Encoder for resume pointers (overflow mode). |
| `splitter` | str | `greedy` | Where to split inline head vs. spilled tail (`ctrl-aware` understands the table's control codes). |
| `splitter-arg` | int/table | — | Splitter parameter. |
| `redirect-back` | bool | false | Emit a resume pointer at the end of the spilled tail. |
| `defer-pointer` | bool | true | Resolve redirect pointers in the fixup phase. |
| `undersized` | str | `error` | Slot too small for the stub: `error` or `preserve` (keep ROM bytes). |
| `slot-measure` | str | next-ptr | `source-entry` = measure slots by ctrl-aware walk instead of pointer distance. |

Script bodies support label fixups: `[FFC0@@label]` (global label),
`[FFC0@N:tag]` / `[label:tag]` (entry-to-entry references) — resolved
during the fixup phase.

## `[extract]`

| key | type | default | description |
|---|---|---|---|
| `default_lang` | str | — | Destination language when `retrotool extract` runs without `--lang`/`--dest`. Point it at the *source* language so extraction can't clobber translations. |

## `[mesen]`

| key | type | default | description |
|---|---|---|---|
| `sync-sram` / `sync_sram` | bool | false | After a build, copy `<saves>/<source>.srm` → `<saves>/<output>.srm` so Mesen2 keeps the save across patched ROMs. |
| `saves-dir` / `saves_dir` | str | platform default | Mesen2 Saves directory override (`~` expanded). |
| `archive-overwritten` / `archive_overwritten` | bool | true | tar.gz a pre-existing destination `.srm` before clobbering. |

## `[editor]`

Configuration for `retrotool edit` (the GUI script editor):
`[editor]`, `[editor.control_codes]`, `[editor.preview]`,
`[editor.files."stem"]`. Fully documented in the README's
**`retrotool edit`** CLI Reference section and in
`retrotool/script/editor_config.py`; zero-config defaults follow
`build_lang` / `<lang>_data_dir` / `tables/*_<lang>.tbl`.

## Variable interpolation & conditions

- `${var}` is substituted in every `[rom.build]` string at parse time
  (except `if=` expressions, interpolated at evaluation time).
- Built-ins: `${name}` (`[rom].name`), `${version}`, `${revision}`,
  `${lang}`; every scalar on `[rom.build]` also becomes a variable.
- CLI `-D name=value` (repeatable) overrides any of them.
- Conditions: `if = "${var}==literal"` or `!=`; whitespace tolerated.

---

# DataDef files

One toml per data block, discovered via `data_dirs`. `[table]` is required;
the other tables are optional. **A `[section]` table (even empty except
`kind`) is what enrolls the DataDef in the build** — without it the DataDef
is extract/documentation-only.

## `[table]`

| key | type | required | default | description |
|---|---|---|---|---|
| `name` | str | yes | — | Identifier. Drives the default script filename `<lang_data_dir>/<name>.txt`, `--only`/`--skip` matching, and `order`. |
| `type` | str | no | `pointer` | `pointer`, `fixed`, `dte`, `bytecode`. |

## `[encoding]`

| key | type | required | default | description |
|---|---|---|---|---|
| `table_file` | str | yes* | — | The `.tbl` used to encode the translation at build time (`retrotool lang new` forks + repoints it per language). |
| `fallback` | str | no | — | Secondary `.tbl` consulted when the primary misses. |
| `terminator` | int | no | 0x00 | End-of-string byte (rbshura: `0xFF`). |

## `[pointers]`

| key | type | required | default | description |
|---|---|---|---|---|
| `offset` | int | yes* | — | **PC file offset** of the pointer table. |
| `count` | int | yes* | — | Number of entries. |
| `size` | int | no | 2 | Pointer width: 2 (within-bank, bank implied by the table's own bank) or 3 (24-bit; relocate routes entries through `freespace`). |
| `bank_override` | int | no | — | Bank byte override for 2-byte pointers whose data lives in a different bank than the table. |

## `[data]`

| key | type | required | default | description |
|---|---|---|---|---|
| `offset` | int | yes* | — | **PC file offset** where the original strings start. |
| `end` | int | no | — | Exclusive end bound; enforced when packing 16-bit-pointer scripts (informational under 24-bit relocate). |
| `compression` | str | no | `none` | `none`, `lzss`, `rle`, `custom`. |
| `compression_params` | table | no | `{}` | Codec parameters. |

## `[relocation]`

| key | type | required | default | description |
|---|---|---|---|---|
| `target` | int | yes* | — | PC offset to relocate the block to. |
| `pointer_size` | int | no | 3 | Pointer width written for the relocated block. |

## `[section]`

Enrolls the DataDef in the build. Keys map onto the inline-section schema:

| key | type | default | description |
|---|---|---|---|
| `kind` | str | required | Usually `script` or `fixed-records`. |
| `en_file` / `file` | str | `<lang_data_dir>/<name>.txt` | Source text override (declaring both is an error). |
| `grow`, `codec`, `if`, `offset`, `cache` | | — | Same semantics as inline sections. |
| `[section.placement]` / `[section.overflow]` | table | project default | Same as inline `[placement]`/`[overflow]`; full-table override, no deep merge. |
| `clobber_lead_entries` | list[int] | `[]` | Windowed-script: entry indices whose leading byte the stub may clobber. |

Forbidden in `[section]` (author them in their home table instead):
`pointer-table`, `pointer-size`, `count` → `[pointers]`; `table`,
`fallback-table`, `terminator` → `[encoding]`; `word-wrap`,
`textbuf-limit`, `stride` → top-level extras.

## Top-level extras

| key | type | description |
|---|---|---|
| `word_wrap` | table | `line_width`, `max_lines`, `entries`, `newline`, `wrap_mode`, `fill_char` (snake_case here; applied when both `line_width` and `max_lines` are set). |
| `entries` | int | Record count for `fixed`-type tables (when `[pointers]` is absent). |
| `block_len` | int | Record stride for fixed-records. |
| `textbuf_limit` | int | Per-entry encoded-byte budget. |
| `[[fields]]` | array | Fixed-records field schema, below. |

## `[[fields]]` (fixed-records)

| key | type | required | description |
|---|---|---|---|
| `label` | str | yes | Field name (appears in the `<<$ADDR:idx.label>>` dump headers). |
| `start` / `len` | int | no | Byte position/length within the record; omit both for auto-pack mode. |
| `fill` | int | no | Padding byte (e.g. `0x20` space). |
| `ptr_writes` | array | no | Recomputed pointer injections: each entry `{addr, count, size, format}` (e.g. `format = "within-bank"`). |

# MBXML Support — Plan

Bring MBuild's MBXML build pipeline into `retrotool`, reuse existing primitives, extend the schema for features retrotool has that MBuild doesn't.

**Target:** `retrotool` 0.8.3 (no compression-engine rework yet — that lands in 0.9).
**Non-goals:** Lunar Compress binding, SMW-specific RLE (BM5/SFCWRLE) ports, Windows file associations.

---

## 1. Architecture

There are **two equivalent front-ends** that produce the same internal `BuildSpec`:

- **MBXML** — one flat file describing every piece of ROM data (MBuild compatibility + retrotool extensions).
- **project.toml + tables/\*.toml** — existing retrotool idiom. Already used by `sfc-lm3-eng/lm3.py`.

Both parse to the same dataclass tree and are driven by the same extract/build runners. A project can also **mix** them — reference an MBXML file from `project.toml`, or have MBXML `<include src="tables/foo.toml"/>` pull in a datadef.

**Two operations** over the `BuildSpec`:

- `retrotool extract <path>` — ROM → files, using each element's extract config.
- `retrotool build <path>` — files → ROM, using each element's build config.

(`<path>` may be a `.mbxml`, a `project.toml`, or a directory containing one.)

New subpackage `retrotool.mbuild` (name chosen to reflect both front-ends; `retrotool.mbxml` nested inside as the MBXML-specific parser):

```
retrotool/mbuild/
  __init__.py
  spec.py                 # BuildSpec + Element dataclasses (the canonical in-memory form)
  codecs.py               # shared codec dispatch (lzss-zamn / rle / planar / …)
  extract.py              # runs extract ops on a BuildSpec (parallelizable)
  build.py                # runs build ops on a BuildSpec (copy-ROM, apply, post-process)
  cache.py                # per-element BuildCache hookup (sha of inputs → skip rebuild)
  overflow.py             # redirection strategies (FFC0-style indirection, freespace alloc)
  roundtrip.py            # extract → build → diff verifier
  diff.py                 # xdelta + IPS generators (build post-process)
  checksum.py             # SNES checksum + padding (reuses retrotool.core)
  front_ends/
    mbxml.py              # .mbxml → BuildSpec
    project_toml.py       # project.toml + tables/*.toml → BuildSpec
    schema.py             # shared per-element shared/extract/build attribute validation
```

`retrotool.extraction`, `retrotool.export`, `retrotool.heuristics` remain **separate tools** — they can *consume* a `BuildSpec` (or a `project.toml` / `DataDef`) but are not invoked from inside the build pipeline.

---

## 2. Feature parity with MBuild 1.29

| MBuild element | retrotool handler | Notes |
|----------------|-------------------|-------|
| `<build>` attrs (`original`, `name`, `version`, `revision`, `revbyteloc`, `path`, `pad`, `diff`) | `BuildSpec` dataclass | 1:1 |
| `<rep>` / `<ins>` | `sections.rep` / `sections.ins` | Raw byte replace / insert. Trivial. |
| `<lzr>` / `<lzi>` | `sections.lz_compress` | **See §4** — only formats retrotool already implements (LZSS presets). Fail gracefully for unsupported types until 0.9. |
| `<rlr>` / `<rli>` | `sections.rle_compress` | Map MBuild `rletype` to our `RLECodec`; drop BM5/SFCW (port later if needed). |
| `<bpr>` / `<bpi>` | `sections.bitplane` | 2BPP↔1BPP-IL conversions — we already have these in `retrotool.graphics.tiles`. Shim. |
| `<sbr>` / `<sbi>` | `sections.script_build` | Text→binary via `retrotool.script.Table`. Existing code. |
| pad="true" | `pipeline._pad_to_next_size` | Reuse `core.rom` header detect + checksum recalc. |
| diff="xdelta" | `diff.xdelta_create` | `subprocess.run(["xdelta3", "-e", …])` — prefer system binary, bundle nothing. |
| diff="ips" | `diff.ips_create` | Pure-python IPS writer (16MB limit, RLE+raw records, EOF). |
| revbyteloc | `pipeline._write_revision_byte` | Simple seek+write. |

**Drop from MBuild:**
- Windows file associations, registry nonsense.
- Bundled `Lunar_Compress.dll` (skipped — no cross-platform solution that's lightweight).
- Hardcoded Marvelous/SBM5 pointer dumpers — those belong in project-specific scripts, not core.

---

## 3. Extensions (retrotool additions MBuild lacks)

**Design principle (corrected):** MBXML is a *data descriptor*. Each element declares a piece of ROM data (where it lives, what format, how to (de)code it). The same document drives **extract** (ROM → files) and **build** (files → ROM). It is not a pipeline runner. Extraction, export (Godot/Tiled/C++), and heuristics are **separate tools** that consume this descriptor (or drop it) — not elements inside it.

### Import/export config lives *on* data elements

Attributes split into three buckets per element:
- **shared** — identify the data: `offset`, `file`, `type`/codec selector.
- **extract-only** — what to do when reading from ROM: `extract-to` (path override), `decode` (codec variant), `format` (output representation, e.g. `png` vs `raw` for graphics).
- **build-only** — what to do when writing to ROM: `encode`, `pad-to`, `allow-grow`, `compression-level`.

Build-only or extract-only attrs on the *other* operation are silently ignored (with a `--strict` flag that turns them into errors).

### New elements (all obey the split above)

| New element | Backing retrotool module | Purpose |
|-------------|--------------------------|---------|
| `<asar>` | `retrotool.asm.AsarPatch` + `apply_patch` | Apply an `.asm` patch via asar. **Build-only element** (no symmetric extract). Attrs: `file`, `includes`, `defines`. |
| `<graphics>` | `retrotool.graphics.tiles` + `.palette` + `superfamiconv` | Tile/palette/tilemap data. Shared: `offset`, `file`, `bpp`, `count`. Extract: `format="png"|"raw"`, `palette-ref`, `tiles-per-row`. Build: `encode="planar"|"packed"`, `compression` (names one of the `<lzr>`-style codecs). |
| `<script>` | `retrotool.script.Table` + `extract_script`/`compile_script` | Text blocks with pointer tables. Shared: `offset`, `pointer-table`, `table-file`, `file`. Extract: `terminator`, `decoding`. Build: `relocation`, `dte-table`. |
| `<bin>` | raw bytes | Plain binary blob. Shared: `offset`, `size`, `file`. Extract: `format="hex"|"raw"`. Build: `pad-to`. Covers cases MBuild does with `<rep>`/`<ins>`. |
| `<project>` | `retrotool.project.load_project` | Top-of-file `<project src="project.toml"/>` — pulls datadefs so later elements can refer to them as `${datadef.main_dialog.pointers.address}`. Not a pipeline step. |
| `<asardef>` | codegen helper | **Build-only.** Auto-generate `!define` symbols from a project datadef so a sibling asar patch can pick up current addresses. |

### Unified `<compression>` attribute

Instead of separate `<lzr>` / `<lzi>` / `<rlr>` / `<rli>` / `<bpr>` / `<bpi>` sections (MBuild's matrix of codec × operation), collapse to: **any data element gets a `codec=` attribute.** The handler decides replace-vs-insert based on size-change detection or an explicit `grow="insert|replace|fail"` attr. MBuild's split stays supported for backward compat but the new style is recommended.

### Cross-cutting extensions:

- **Variable interpolation**: `${env.FOO}`, `${build.path}`, `${datadef.main_dialog.pointers.address}` allowed in any attribute. MBuild has none; we need them for reusable configs.
- **Include**: `<include file="shared.mbxml"/>` so projects can split builds.
- **Conditionals**: `<lzr if="${version}==english">…</lzr>` — simple string equality, no expressions. Enables multi-locale builds from one file.
- **Caching**: Reuse `retrotool.core.BuildCache`. Each section keyed by (handler, attrs, file sha256, input sha). Rebuilds skip unchanged sections. MBuild always runs everything.
- **Dry-run**: `retrotool build --dry-run` prints planned actions without touching disk.
- **JSON config alternative**: Same schema expressed as TOML or JSON for users who dislike XML. Parser dispatches by file extension.

---

## 3a. Pipeline patterns inherited from `lm3.py`

The canonical per-game build driver today is `/mnt/crucial/projects/sfc-lm3-eng/lm3.py`. It's a pile of LM3-specific logic, but the *patterns* it uses are the ones the generalized `retrotool.mbuild` pipeline must support. Refactoring LM3 to use the generalized pipeline is the acceptance test for this plan.

Patterns to generalize (all go into `retrotool.mbuild`, not MBXML-specific):

- **Cache-aware encoding.** Each element has an input-side hash (source file SHA + codec config). If the cache has a binary for that hash, skip re-encoding. `retrotool.core.BuildCache` already exists; wire it per-element.
- **Parallel pre-encoding.** CPU-bound encoders (script compile, font raster, LZ compress) run in a `ProcessPoolExecutor` before the serial ROM-write phase. Element handlers expose a `prepare()` method (pure, parallelizable) separate from `apply(rom)` (serial, mutates ROM).
- **Pointer-table rewriting + overflow redirection.** When a script block overflows its original region, redirect overflow entries to a configured expansion area (LM3 uses FFC0 pointer indirection in bank $C6). Generalize as:
  - `<overflow bank="$C6" strategy="ffc0-indirect"/>` attr on a `<script>` element, or
  - `[overflow]` table in a per-datadef TOML.
  The concrete strategies (`ffc0-indirect`, `freespace`, `chained`, `fail`) are pluggable so games with different conventions can add their own.
- **Duplicate-entry deduplication.** Share a single pointer for identical entries (LM3 does this for empty strings). Element-level `dedupe="true"` attr.
- **Fixed-record tables.** `<fixed-records>` element with `stride`, `count`, `fields=[…]`. Validates width at build time. LM3's unit names / items / equipment fall into this.
- **Word-wrap-aware text encoding.** Encoder takes a `max-width` attr and reports line-break points; overflow at encode time is an error, not a silent truncation.
- **ROM-size padding.** Post-process step bumps ROM to the next power-of-two SNES size and patches the header size byte ($7FD7 for LoROM). Already in MBuild as `pad="true"`; extend with explicit target like `pad-to="4M"`.
- **Round-trip verify.** Built-in subcommand `retrotool mbuild <path> --verify` extracts, re-builds, byte-diffs against original. Any non-zero diff is a failure. LM3 has a one-off `verify` subcommand; we make it free for every project.
- **Parallel subcommand dispatch.** LM3's `font` / `script` / `vwf` / `build` subcommands become phases of the same spec: `retrotool mbuild … --only=fonts,scripts` runs only those elements. `--skip=vwf` inverts it.

## 4. Compression in 0.8.3 (scoped carefully)

Only support LZ formats retrotool already implements:
- `lztype="lzss-rbshura"` → `LZSSCodec(PARAMS_RBSHURA)`
- `lztype="lzss-zamn"` → `LZSSCodec(PARAMS_ZAMN)`
- `lztype="lzss-legacy"` → `LZSSCodec(PARAMS_LEGACY)`
- `lztype="rle"` → `RLECodec()`

If an MBXML references an `lztype` we don't support (numeric Lunar types, BM5, SFCW):
- Emit a clear error naming the format + link to 0.9 roadmap.
- Exit code 2 (config error).

0.9 series will add: LC_LZ1/2/3/16/19 reimplementations (most common SMW/SoM/Kirby formats), then the rest incrementally.

---

## 5. Phases & milestones

**Phase 1 — Spec + parser** ✅ **DONE** (2026-04-14)
- Write `schema.py` with XSD-style validation (catch typos, wrong types, unknown attrs).
- `mbxml.py` → `BuildSpec` dataclass + `Section` union. Parse ALL MBuild 1.29 element types, even if handler not yet written.
- Unit tests on `MBuild.MBXML` from the `/mnt/crucial/projects/MBuild/` directory — should parse clean.
- **Added during phase:** auto-migration of legacy MBuild 1.29 codec-matrix elements (`<lzr/lzi/rlr/rli/bpr/bpi/sbr/sbi>`) to unified `<bin codec= grow=>` / `<graphics encode=>` / `<script grow=>` in-memory. `MBXMLDeprecationWarning` emitted per legacy element. `migrate_mbxml(path, in_place=True)` rewrites the XML file on disk. `Section.original_kind` retains provenance.

**Phase 2 — Core build path** ✅ **DONE** (2026-04-14)
- `build.py`: copy ROM, iterate elements, call build handlers, post-process (pad/checksum/revbyte/diff).
- Implement `rep`, `ins`, `sbr`, `sbi`, `<bin>`, `<script>` (build side) handlers.
- Smoke test: build a trivial MBXML that does byte replacements.
- **Note:** pure-python checksum (no superfamicheck dep). `sbr`/`sbi` fold into the unified `<script>` handler post-migration.

**Phase 2b — Symmetric extract path** ✅ **DONE** (2026-04-14)
- `extract.py`: iterate elements, call extract handlers, dump files.
- Same element dataclasses, opposite direction. Handlers share codec registry.
- Smoke test: round-trip — extract from a ROM, rebuild, diff the result is zero.
- **Sizing strategy:** `section.size > existing-file-size > error`. Multi-file `<ins>` uses per-file existing sizes.

**Phase 2c — `project.toml` front-end** (pulled forward from old Phase 6b)
- `front_ends/project_toml.py` — parse `[mbuild]` table in project.toml into the same `BuildSpec` the MBXML parser produces.
- Same handlers, build, extract: zero changes downstream.
- Smoke: trivial project.toml drives the same round-trip as the MBXML equivalent.
- **Rationale:** land both front-ends before Phase 3 so compression/bitplane work covers both input formats uniformly.

**Phase 3 — Compression + bitplane** ✅ **DONE** (2026-04-14, partial)
- `<bin codec=...>` dispatches via `retrotool.compression.registry` (lzss-zamn, lzss-rbshura, lzss-legacy, rle). Build compresses; extract decompresses.
- `<graphics>` handler wired; raw/planar passthrough implemented.
- Bitplane transform registry scaffolded (`_BITPLANE_TRANSFORMS`) — legacy MBuild bptype values (e.g. `2bpp-to-1bpp-il`) raise `HandlerError` pointing to the not-yet-implemented state. Real conversions land in a later phase without touching the handler contract.
- **Gotcha documented:** codecs without self-terminating markers (`lzss-legacy`, `rle`) over-read ROM padding during extract. Fix: when `section.size` is set, the extractor slices the ROM to that bound before decompress. Self-terminating codecs (`lzss-zamn`, `lzss-rbshura`) work without `size`.

**Phase 4 — Diff output** ✅ **DONE** (2026-04-14)
- `diff.py`: IPS writer (pure python, 16MB limit, RLE at ≥13-byte runs, handles growth past original end) + `apply_ips` for round-trip verification.
- xdelta3 subprocess wrapper with graceful fallback: `write_xdelta(..., required=False)` returns `DiffResult(skipped=True, note=install-hint)` when binary absent.
- `build()` reads `spec.diff` ∈ {"ips","xdelta","both"} (comma-separated also accepted), writes patches next to the ROM, populates `BuildResult.diffs`.
- Companion wheel `retrotool-xdelta` (bundling xdelta3, Apache-2.0) scaffolded at `packages/retrotool-xdelta/` with CI `.github/workflows/wheels-xdelta.yml`. `diff.write_xdelta` prefers bundled binary, falls back to system `xdelta3`. Pending: `vendor/xdelta` submodule add (`github.com/jmacd/xdelta`) + pin, first tagged build `xdelta-v0.1.0`.

**Phase 5 — retrotool extensions** ✅ **DONE** (2026-04-14)
- 5.1 Variable interpolation: `${var}` syntax, `defines=` kwarg overrides built-ins, vars persisted on `BuildSpec.vars` for build-time re-evaluation.
- 5.2 `if=` conditionals: `==`/`!=` only (intentional), false sections recorded in `BuildResult.skipped[]`.
- 5.3 `<include src=…>`: recursive splice, parent vars win, cycle detection via resolved-Path seen-set.
- 5.4 `<asar>` handler: round-trips through tempfile, attrs `includes`/`defines` are pipe-separated.
- 5.5 `<project src=…>` handler: nested mbxml against parent ROM canvas, sub-spec vars are local scope.
- 5.6 BuildCache hookup: keyed on (kind + sorted attrs + input-file SHAs), only REP/INS/BIN/GRAPHICS/SCRIPT cacheable (asar/project write whole ROM). `BuildResult.cache_hits`, `SectionResult.cache_hit`.
- Companion wheel: `retrotool-asar` scaffolded (RPGHacker/asar v1.91, MIT, CMake) at `packages/retrotool-asar/`, CI `wheels-asar.yml`. `retrotool.asm.patcher.apply_patch` resolves bundled → PATH.
- Tests: 98 mbuild pass.

**Phase 6 — CLI + docs** ✅ **DONE** (2026-04-14)
- `retrotool build / extract / migrate` subcommands in `retrotool.cli`.
  `<path>` accepts `.mbxml` / `.toml` / directory (project.toml wins).
  Flags: `-o`, `--no-cache`, `--diff`, `--dest`, `--in-place`.
- README gained an **MBXML builds** section: MBuild 1.29 compat + auto-migration
  note, retrotool extension table (unified `<bin codec=>`, `<graphics>`,
  `<script>`, `<asar>`, `<project>`, interpolation, `if=`, `<include>`, cache,
  diff), project.toml front-end note.
- `examples/mbxml/demo.mbxml` + `shared.mbxml` + `README.md` exercise the
  retrotool-only extensions. Parser smoke-tested clean.

**Phase 6b — Pipeline patterns** ✅ **DONE** (2026-04-14)
- `--only`/`--skip` phase-filter flags on `retrotool build|extract` —
  comma-separated kind list, filtered sections land in `BuildResult.skipped`.
- `[mbuild].include = […]` in project.toml splices sibling TOMLs (cycle-detected).
- `prepare()/apply()` split via `retrotool/mbuild/prepare.py`. CPU-bound
  encoders for BIN(with codec)/GRAPHICS/SCRIPT run in a `ProcessPoolExecutor`
  via `parallel_prepare(spec, files_root, max_workers=...)`. Workers return
  bytes; main process stuffs `section._prepared` (transient attr); handlers
  honor it and skip re-encoding. Wired through `build(parallel=N)` +
  `retrotool build -j N`.
- `retrotool/mbuild/overflow.py` registry + strategies: `fail`, `truncate`,
  `inline-redirect`. The last is a generic version of LM3's `FF C0 ll mm hh`
  mechanism — configurable marker bytes, pointer encoder (default 24-bit
  LoROM LE), splitter callable (`split_at_last_marker_byte` for window-aware
  splits like LM3's `[P]=0x10`), optional `redirect_back` tail. Paired
  `FreespaceAllocator` (bump over `[(lo,hi),...]` ranges). Handler integration
  is Phase 6c work — strategies stand alone with full unit coverage now.
- `<fixed-records>` element: `stride × count` validator + raw-binary
  build/extract. Both front-ends + schema updated. Structured-field encoder
  (TOML records → packed bytes per `fields=` schema) is follow-on.
- `dedupe="true"` attr already lived on `Section` from Phase 1; consumer
  semantics belong to whichever variable-length handler invokes it (LM3
  refactor will exercise it on `<script>`).
- `cache.py` per-element hookup already shipped in Phase 5 — left as-is.
- Tests: 124 mbuild pass (+26 over Phase 6).

**Phase 6c — LM3 refactor (acceptance test)** (1 day)
- Port `sfc-lm3-eng/lm3.py` to use `retrotool.mbuild` exclusively. Remaining LM3-specific code should be limited to:
  - address constants in `project.toml`,
  - the VWF/debug asar patches (already external),
  - any custom overflow strategy registered as a plugin.
- Round-trip `retrotool mbuild . --verify` against original `lm3.sfc` must pass.
- If any lm3.py logic can't be expressed via the generalized pipeline, we either extend the spec (preferred) or accept a LM3-scoped extension module (escape hatch).

**Phase 7 — Ship 0.9**
- CHANGELOG entry.
- Version bump.
- Tag + release.

Total estimate: ~7 workdays for full scope including LM3 refactor. Phases 1-4 alone give a usable MBuild-compatible builder in ~3 days.

---

## 6. Open questions

1. XML library choice: stdlib `xml.etree.ElementTree` (zero deps, no XSD) vs `lxml` (has XSD, but binary dep). Lean stdlib + handroll schema validator for now.
2. Do we mirror MBuild's output-filename convention (`{name}_{version}.{revision:02}{ext}`) as default, or let users override via new attr? → Keep default, add `output=` attr to override.
3. Should `<project>` be allowed mid-build or only at top? → Top only for now, single project per build.
4. Endianness/signed-hex MBuild quirks: MBuild accepts only hex. We should accept decimal + hex + SNES notation (`$C1:8000`) via existing `parse_snes_addr`/`integer_or_hex` helpers.

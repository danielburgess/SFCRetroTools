# retrotool — engineering reference

Deep-dive companion to [`README.md`](README.md). The README is the
public surface (install, features, vision). This file is the **research
archive** — cross-project inventory, format findings, pipeline
sketches — plus the unshipped work items that don't belong on the
public roadmap yet.

**Package**: `retrotool` (PyPI) | **Repo**: `github.com/danielburgess/SFCRetroTools`
**Current**: 9.2.0 — full toolchain + MBXML + libSFX integration shipped.
**Active design docs**: [`plans/`](plans/) — one file per in-flight release plan.

For the user-facing vision (project workbench, patch manager, community
patch index) see [README § Where this is going](README.md#where-this-is-going).

---

## Existing Asset Inventory

Research cache from the initial consolidation pass. Most rows are
**already reused** in retrotool; the table still has value as a map
from the source project to the consolidated module, and as a list of
open candidates for the next round of ports.

### Reusable Components Found

| Component | Source | Lines | Reuse Value |
|-----------|--------|-------|-------------|
| SNES addr conversion (LoROM/HiROM/Ex) | lm3 `retrotool/snes.py` | 579 | **Core** — direct port (shipped in `core.address`) |
| SNES addr conversion (HiROM/FastROM) | rbshura `rbshura.py` | 645 | **Core** — merged w/ lm3 version |
| Table file parser + text codec | lm3 `retrotool/script.py` | 400+ | **Core** — generalized (shipped in `script.table`) |
| Text tokenizer + control codes | rbshura `text_tool.py` | 683 | **Core** — merged; different ctrl code system |
| Pointer table heuristic scanner | lm3 `snes_ptr_finder.py` | 1200 | **Core** — generalized (`heuristics.pointers`) |
| Mesen IPC client (lm3) | lm3 `mesen_ipc.py` | 53 | **Core** — expanded (`debugger.client`) |
| Mesen IPC client (rbshura) | rbshura `mesen_ipc.py` | 39 | **Core** — same protocol, merged |
| Live text finder via IPC | lm3 `text_finder.py` | 150 | **Reuse** pattern |
| LZSS decompressor (ZAMN variant) | zamn `zamn_rom.py` | 1922 | **Core** — parameterized LZSS |
| LZSS compress+decompress (RBShura) | rbshura `rbshura.py` | ~160 | **Core** — second LZSS variant (HiROM, 0xFEE init) |
| LZSS legacy decompressor | rbshura `lzss_old.py` | 190 | **Reference** — multi-reset variant |
| Compressed block scanner | rbshura `rbshura.py` | — | **Core** — heuristic LZSS block finder |
| 4BPP planar tile decoder | zamn `zamn_to_godot.py` | — | **Core** — generalized to 2/4/8BPP |
| 2BPP planar tile decoder | rbshura `rbshura.py` | — | **Core** — merged into unified tile module |
| 4BPP planar tile decoder (rbshura) | rbshura `rbshura.py` | — | **Core** — merged into unified tile module |
| Sprite composition + atlas | zamn `zamn_to_godot.py` | — | **Core** — generalized |
| Animation extraction (4 methods) | zamn `zamn_to_godot.py` | — | **Reference** — pattern library |
| Collision map extraction | zamn `zamn_to_godot.py` | — | **Core** — generalized |
| Level → Godot scene gen | zamn `zamn_to_godot.py` | 4036 | **Reference** — extract patterns |
| Godot .tscn/.tres generators | zamn `zamn_to_godot.py` | — | **Core** — standalone module |
| Object registry (entity DB) | zamn `zamn_object_registry.json` | — | **Pattern** — project schema |
| ASM patch pipeline (asar) | lm3 `lm3.py` | — | **Core** — generalized (`asm.patcher`) |
| Build caching (SHA-256) | lm3 `lm3.py` | — | **Core** — standard infra (`core.cache.BuildCache`) |
| Asar Python bindings | asar `src/asar-dll-bindings/python/` | — | **Direct** use (bundled via `retrotool-asar` wheel) |
| Font generator (pixel-level glyph defs) | rbshura `fonts/build_en_font.py` | 1574 | **Core** — pending `graphics.font` port |
| Translation preview (pywebview) | rbshura `preview.py` | 809 | **Reference** — interactive preview pattern for GUI |
| Tile extractor + classifier | tile_extractor `*.py` | ~1000 | **Core** — pending merge (perceptual hash + K-means) |
| Godot tileset/tilemap export | tile_extractor `godot_exporter.py` | — | **Core** — merged w/ zamn exporter |
| Script data block parser | rbshura `rbshura.py` | — | **Core** — CopyDataToRAM format |

### Mesen2-Diz IPC Capabilities (50+ cmds)

- Memory R/W, CPU state, breakpoints, step/trace, reverse execution
- Controller input automation, save states, expression eval
- Disassembly search, CDL data, call stack
- Label management w/ categories

---

### Key Cross-Project Patterns (my personal list)

| Pattern | lm3 | rbshura | zamn | tile_ext | Overlap |
|---------|-----|---------|------|----------|---------|
| SNES addr math | LoROM | HiROM | LoROM | — | **Unified** |
| LZSS decomp | — | Custom | ZAMN variant | — | **Parameterized** |
| 2BPP tiles | — | Yes | — | — | **Merged** |
| 4BPP tiles | — | Yes | Yes | — | **Merged** |
| `.tbl` parser | Yes | Yes | — | — | **Unified** |
| Mesen IPC | Yes | Yes | Yes (docs) | — | **Single client** |
| Text extraction | Pointer-based | FF-delimited | — | — | **Both patterns** |
| Font pipeline | 1BPP-IL VWF | 2BPP 16x16 | — | — | **Pending generalize** |
| Godot export | — | — | .tscn/.tres | .tres/.tscn | **Merged** |
| Tile classify | — | — | — | K-means | **Pending integrate** |

---

## Project File Format

### Example files shipped in this repo

Concrete, runnable references — copy any of these as the starting
point for a new project:

| Path | Format | What it shows |
|------|--------|---------------|
| [`examples/mbxml/demo.mbxml`](examples/mbxml/demo.mbxml) | MBXML | Retrotool extensions over MBuild 1.29: unified `<bin codec=>`, `<graphics>`, `<script>`, `<asar>`, `${var}` interpolation, `if=` conditionals, `<include>`. Build with `VERSION=v1 LOCALE=en retrotool build demo.mbxml`. |
| [`examples/mbxml/shared.mbxml`](examples/mbxml/shared.mbxml) | MBXML | Demonstrates the `<include>` mechanism — sections defined here splice into `demo.mbxml`. |
| [`examples/mbxml/README.md`](examples/mbxml/README.md) | walkthrough | Build / extract / migrate command reference. |
| [`examples/libsfx-hello/hello.mbxml`](examples/libsfx-hello/hello.mbxml) | MBXML | Two-section spec — `<libsfx src=…>` builds a libSFX project as the working ROM canvas, then `<rep>` patches bytes on top. |
| [`examples/libsfx-hello/README.md`](examples/libsfx-hello/README.md) | walkthrough | 4-step end-to-end: scaffold → CLI build → MBXML build → Mesen load. Includes Python API equivalents. |

A `project.toml` example shipping in `examples/` is **still pending**
(tracked under "open CLI/examples work" below). For a real-world
reference today, see `/mnt/crucial/projects/sfc-lm3-eng/project.toml` —
the LM3 translation project that drove the original retrotool
consolidation. The schema below is the canonical shape both shipped
and pending examples will use.

### `project.toml` — Central Definition

```toml
[rom]
name = "Little Master III"
file = "lm3.sfc"
mapping = "lorom"           # lorom|hirom|exlorom|exhirom|sa1
size = "2M"
expanded_size = "4M"
header = false

[rom.vectors]
reset = "$80:FFFE"
nmi = "$80:FFEA"
irq = "$80:FFEE"

[rom.sram]
start = "$70:0000"
size = "8K"

[rom.hardware]
coprocessor = "none"        # none|superfx|sa1|dsp1|cx4|sdd1|spc7110

[build]
assembler = "asar"
output_dir = "out/"
cache_dir = ".cache/"

[debugger]
type = "mesen-diz"
pipe_name = "auto"
```

### Per-Table Data Definition (`scripts/main_dialog.toml`)

Convention: drop one TOML per logical data block under a project
subdirectory (`scripts/`, `tables/`, `gfx/`, `levels/`). The root
`project.toml` references them via `[mbuild].include = […]`. A live
example of this layout is `/mnt/crucial/projects/sfc-lm3-eng/scripts/`.


```toml
[table]
name = "main-dialog"
type = "pointer"            # pointer|fixed|dte|bytecode

[encoding]
table_file = "tables/eng.tbl"
fallback = "tables/jap.tbl"
terminator = 0x00

[pointers]
address = "$1B:8000"
count = 512
size = 2                    # bytes per pointer (2 or 3)
bank_override = "$1B"

[data]
start = "$1B:8400"
compression = "none"        # none|lzss|rle|custom

[relocation]
target = "$C1:8000"
pointer_size = 3

[display]
word_wrap = { width = 26, lines = 6 }
windowed = true
```

---

## LZSS Compression Variants (key finding)

ZAMN + RBShura use LZSS with the same core algorithm but different parameters:

| Parameter | ZAMN | RBShura | Parameterized |
|-----------|------|---------|---------------|
| Window size | 4096 (12-bit) | 4096 (12-bit) | `window_bits=12` |
| Init fill byte | 0x20 (space) | 0x00 | `fill_byte` |
| Init write pos | 0xFEE | 0xFEE | `init_pos=0xFEE` |
| Min match | 3 | 3 | `min_match=3` |
| Max match | 18 | 18 | `max_match=18` |
| Size header | 16-bit LE, bit15=chain | 16-bit LE | `chained=bool` |
| Ctrl bit order | LSB-first | MSB-first | `bit_order='lsb'/'msb'` |
| Back-ref format | `[lo8, (hi4<<4)\|len]` | `[lo8, (hi4<<4)\|len]` | same |

→ Single `LZSSCodec(params)` class handles both. Projects declare their
params in `project.toml`. **Necrofy** (C#) has a third impl — same algo,
confirms parameters for ZAMN.

**Discovery workflow**: play game in Mesen → breakpoint on DecompressData
routine → read params from registers → auto-generate codec config. See
Phase 16 below.

---

## Level Map Extraction (w/ collision, triggers, logic)

| Layer | Data | Export Format |
|-------|------|---------------|
| **Background tiles** | Metatile indices → tile IDs | Tiled TMX layer / Godot TileMapLayer |
| **Foreground tiles** | Priority-split tiles | Separate layer w/ z-order |
| **Collision map** | Per-tile collision flags (solid/water/hazard/slope/ladder) | Tiled object layer / Godot physics layer |
| **Triggers** | Door transitions, warp zones, event triggers | Tiled object layer w/ properties / Godot Area2D nodes |
| **Spawn points** | Entity positions + types + delays | Tiled object layer / Godot Marker2D + metadata |
| **Associated logic** | Spawner behavior, area constraints, one-shot vs respawn | GDScript stubs / C++ enum exports / JSON behavior defs |
| **Palette zones** | Per-level palette selections, animated palettes | Metadata in TMX properties / Godot shader params |

### Collision Extraction Pipeline

1. Read collision table from ROM (per-tileset, typically 512 entries × 2 bytes).
2. Map collision type flags → semantic categories.
3. Generate per-metatile collision shapes (combining 8×8 tile collision into 64×64 metatile).
4. Export as physics polygons (Godot) or object rectangles (Tiled).
5. Trigger detection via heuristic: scan for warp/transition pointer tables near level data.

---

## Implementation Phases — status

Phases 1–12 (library foundations, codecs, script, debugger, heuristics,
asm, extraction models, export emitters, AI scaffolding) are **shipped
and tested**. Phase 13 (CLI) and Phase 14 (reference examples) are
partially shipped — `retrotool build|extract|migrate|libsfx …` is live
and `examples/mbxml/` + `examples/libsfx-hello/` walk through the
pipelines. Remaining CLI breadth (`retrotool gfx …`, `retrotool heur …`,
`retrotool debug …`) and the per-game reference projects are the
open CLI/examples work.

Unshipped work items worth keeping in writing:

### Phase 15 — GUI Shell

Goal: desktop app that hosts retrotool operations with game-specific
editor panels. Candidates:
- **PySide6 (Qt)** — mature, rich widget set, good table/hex editing, embedded docks.
- **pywebview + local HTTP** — reuses the translation-preview pattern from rbshura; friendlier for custom visual editors (CSS/canvas), slower for large tables.

**Core panels:**
- **Project explorer** — wing-style tree (ROM, datadefs, tables, patches, assets, builds).
- **Game-specific script editor** — table-aware text editor with:
  - Per-row original vs translated columns
  - Live byte-budget indicator (warns before overflow)
  - Control-code palette from the table file
  - DTE candidate highlighter
  - Windowed/word-wrap preview matching in-game renderer
  - Search/replace with scope filters (datadef, range of IDs, regex)
- **Pointer table inspector** — sorted/unsorted view, jump-to-target, detect orphans, detect shared targets, resize-and-relocate wizard wired to asm/freespace.
- **Graphics extractor panel** — for any ROM offset:
  - BPP selector (1/2/4/8) + tile-count/stride sliders with live preview
  - Palette dropdown scanned from ROM via heuristic
  - Tilemap overlay mode: render actual in-ROM metatiles on top of decoded gfx
  - Export to PNG, Godot TileSet, Tiled TSX directly
  - Sprite builder: drag 8x8 tiles to compose frame; save to atlas
- **Hex editor (built-in)** — memory-mapped views over ROM + optional live bank from debugger:
  - Side-by-side ROM vs RAM columns, auto-sync on scroll
  - Cell coloring by region classification (code/text/gfx/compressed/pointer_table/unknown) from `heuristics.mapper`
  - Inline compression trial: right-click → "try decompress as <preset>" → preview
  - Bookmarks + labels imported from Mesen via IPC
  - Overwritable with asar patch staging (dirty cells highlighted)
  - Poll modes: snapshot, on-break, N-Hz streaming
- **Debugger control** — Mesen-Diz connection manager:
  - Pipe discovery (scan `/tmp/CoreFxPipe_*` + `\\.\pipe\Mesen2Diz_*`)
  - CPU state watch, breakpoint list, callstack
  - Expression evaluator shelf (persisted per project)
  - Controller input recorder/playback
  - Save-state timeline scrub
- **Heuristic runner** — one-click scans against current ROM and/or captured debugger state; findings pane promotes candidates into `project.toml` datadefs.
- **Asar build panel** — triggers `asm.apply_patch`, shows cache hits, tailed build log.
- **Diff panel** — compare current ROM vs original baseline, filtered by region classification.
- **Patch manager** — see README § Where this is going. Resolves checksum → patch index → apply/stage.

Status: not started. Likely a new `retrotool-gui` sibling package
depending on `retrotool` core, to keep the library dep-free.

### Phase 16 — Runtime-Guided Heuristics

Ratchet up detection accuracy by combining static ROM heuristics with
live debugger state.

- **Pointer-table discovery via memory access patterns**
  - Set a write breakpoint on a suspected text buffer / tilemap buffer.
  - Capture source PC + operand ROM address on each write.
  - Cluster operand addresses by stride → infer pointer-table base, entry size, length.
  - Walk back from the hit PC to the source register and find the table origin.
- **Data block localization by DMA trace**
  - Tap DMA/HDMA register writes via breakpoint on `$4300-$437F`.
  - For each transfer record `(source_bank:addr, dest, size)` tuples.
  - Classify destinations: VRAM → graphics, CGRAM → palette, OAM → sprites, WRAM → working data.
  - Feed back into `heuristics.mapper` as high-confidence regions.
- **Text system discovery by glyph-write correlation**
  - Put write breakpoint on tilemap VRAM range corresponding to text window.
  - Intercept source byte + glyph tile index written per call.
  - Auto-derive `.tbl` mapping: observed_byte → glyph_tile → ASCII char (human-in-the-loop glyph labeling or OCR).
- **Compression scheme fingerprinting**
  - Breakpoint on reset vector's early copy routines.
  - Detect ring-buffer writes (repeated 0x1000-byte-modular writes with 0xFEE start) → LZSS family.
  - Extract fill byte by observing initial ring state (`$0000-$0FFF` WRAM after reset but before decompress).
  - Present confirm/reject to user, register as named preset in project.
- **Behavior / state-machine extraction**
  - Tracelog-based: record `(PC, A, X, Y, P, DB, PB)` per instruction for N frames.
  - Build control-flow graph of entity handler; identify loops and branches that correspond to AI states.
  - Emit skeleton `Behavior` dataclass with state stubs for manual annotation.
- **Coverage-driven classification**
  - Use Mesen CDL (code/data logger) export via IPC to separate executed code from unreached data.
  - Refine `Region` classifications — `executed` overrides `unknown` / `code`.
- **Playthrough-derived discovery sessions**
  - Record an input playback, walk through level transitions, save-state each transition.
  - Diff memory between save-states to locate per-level data pointers.
  - Correlate save-state PCs with pointer-table candidates to auto-bind levels ↔ data.

Status: not started. Depends on Phase 6 hardening (live IPC validation)
and Phase 15 GUI integration for human-in-the-loop confirmation.

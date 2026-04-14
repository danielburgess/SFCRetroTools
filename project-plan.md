# retrotool v0.8.1 — SNES/SFC ROM Hacking Toolkit

**Package**: `retrotool` (PyPI, owned) | **Repo**: `github.com/danielburgess/SFCRetroTools`
**Current**: v0.1.1 (address conversion + table parser, ~890 lines)
**Target**: v2.0.0 — full toolkit rewrite per this plan

## current → v1 Migration

v0.1.x contents (existing):
- `retrotool/snes.py` (578 lines) — SFCAddress, SFCPointer, LoROM/HiROM/Ex conversions
- `retrotool/script.py` (312 lines) — Table class, script dumping

v0.8 placement:
- `retrotool/snes.py` → `retrotool/core/address.py` + `retrotool/core/pointer.py`
- `retrotool/script.py` → `retrotool/script/table.py` + `retrotool/script/extractor.py`

Backward-compat shims (one release):
- `retrotool/snes.py` — re-exports from `core.address` + `core.pointer`
- `retrotool/script.py` — re-exports from `script.table` + `script.extractor`

Consumers to migrate:
- `sfc-lm3-eng/retrotool/` (vendored copy, diverged — has additions to merge back)
- `sfc-rbshura/` — uses local `rbshura.py` for same functions, migrate to retrotool imports

---

## Existing Asset Inventory

### Reusable Components Found

| Component | Source | Lines | Reuse Value |
|-----------|--------|-------|-------------|
| SNES addr conversion (LoROM/HiROM/Ex) | lm3 `retrotool/snes.py` | 579 | **Core** — direct port |
| SNES addr conversion (HiROM/FastROM) | rbshura `rbshura.py` | 645 | **Core** — merge w/ lm3 version |
| Table file parser + text codec | lm3 `retrotool/script.py` | 400+ | **Core** — generalize |
| Text tokenizer + control codes | rbshura `text_tool.py` | 683 | **Core** — merge; different ctrl code system |
| Pointer table heuristic scanner | lm3 `snes_ptr_finder.py` | 1200 | **Core** — generalize |
| Mesen IPC client (lm3) | lm3 `mesen_ipc.py` | 53 | **Core** — expand massively |
| Mesen IPC client (rbshura) | rbshura `mesen_ipc.py` | 39 | **Core** — same protocol, merge |
| Live text finder via IPC | lm3 `text_finder.py` | 150 | **Reuse** pattern |
| LZSS decompressor (ZAMN variant) | zamn `zamn_rom.py` | 1922 | **Core** — parameterized LZSS |
| LZSS compress+decompress (RBShura) | rbshura `rbshura.py` | ~160 | **Core** — second LZSS variant (HiROM, 0xFEE init) |
| LZSS legacy decompressor | rbshura `lzss_old.py` | 190 | **Reference** — multi-reset variant |
| Compressed block scanner | rbshura `rbshura.py` | — | **Core** — heuristic LZSS block finder |
| 4BPP planar tile decoder | zamn `zamn_to_godot.py` | — | **Core** — generalize to 2/4/8BPP |
| 2BPP planar tile decoder | rbshura `rbshura.py` | — | **Core** — merge into unified tile module |
| 4BPP planar tile decoder (rbshura) | rbshura `rbshura.py` | — | **Core** — merge into unified tile module |
| Sprite composition + atlas | zamn `zamn_to_godot.py` | — | **Core** — generalize |
| Animation extraction (4 methods) | zamn `zamn_to_godot.py` | — | **Reference** — pattern library |
| Collision map extraction | zamn `zamn_to_godot.py` | — | **Core** — generalize |
| Level → Godot scene gen | zamn `zamn_to_godot.py` | 4036 | **Reference** — extract patterns |
| Godot .tscn/.tres generators | zamn `zamn_to_godot.py` | — | **Core** — standalone module |
| Object registry (entity DB) | zamn `zamn_object_registry.json` | — | **Pattern** — project schema |
| ASM patch pipeline (asar) | lm3 `lm3.py` | — | **Core** — generalize |
| Build caching (SHA-256) | lm3 `lm3.py` | — | **Core** — standard infra |
| Asar Python bindings | asar `src/asar-dll-bindings/python/` | — | **Direct** use |
| Font generator (pixel-level glyph defs) | rbshura `fonts/build_en_font.py` | 1574 | **Core** — generalize font pipeline |
| Translation preview (pywebview) | rbshura `preview.py` | 809 | **Reference** — interactive preview pattern |
| Tile extractor + classifier | tile_extractor `*.py` | ~1000 | **Core** — perceptual hash dedup, K-means classify |
| Godot tileset/tilemap export | tile_extractor `godot_exporter.py` | — | **Core** — merge w/ zamn exporter |
| Script data block parser | rbshura `rbshura.py` | — | **Core** — CopyDataToRAM format |

### Mesen2-Diz IPC Capabilities (50+ cmds)

- Memory R/W, CPU state, breakpoints, step/trace, reverse execution
- Controller input automation, save states, expression eval
- Disassembly search, CDL data, call stack
- Label management w/ categories

---

## Additional Projects Discovered

### sfc-rbshura (Rushing Beat Shura — `/mnt/data/Projects/sfc-rbshura/`)
- **Game**: Rushing Beat Shura (SFC fighting game), HiROM/FastROM, 2MB
- **LZSS codec**: Both compress + decompress, 4KB window, 0xFEE init pos — second variant vs ZAMN
- **Text system**: FF-delimited blocks, control codes F7-FF, ~512 strings across 3 ROM regions
- **Font pipeline**: 2BPP 16x16 chars, pixel-level glyph generator (`build_en_font.py`, 1574 lines)
- **Graphics**: 2BPP + 4BPP tile decoders, PPM export, scene-based extraction
- **Translation preview**: pywebview app rendering JP/EN side-by-side with actual game fonts
- **Compressed block scanner**: Heuristic LZSS block finder across ROM regions
- **Script data blocks**: CopyDataToRAM format parser (runtime data copy structures)
- **Mesen IPC**: Same protocol as lm3 (shared pattern, slightly different wrapper)
- **Disassembly**: 173,500 lines of 65816 ASM from Mesen2-Diz export

### tile_extractor (`/mnt/crucial/projects/tile_extractor/`)
- **Pipeline**: Image → tile slice → perceptual hash dedup → K-means classify → atlas pack → Godot export
- **Classifier**: 35-element feature vectors (HSV histograms, edge density, entropy)
- **Dedup**: ImageHash library with configurable Hamming distance
- **Godot export**: .tres TileSet + .tscn TileMap generation, per-group atlas sources
- **Already tested**: ZAMN levels 0-5 + 40+ individual levels

### Necrofy (`/mnt/crucial/projects/Necrofy/`)
- **ZAMN-specific**: Level editor + asset exporter (C# .NET WinForms)
- **ZAMN LZSS**: Third implementation of same compression family (C#)
- **Asset types**: 10 types (levels, graphics, tilemaps, palettes, collision, sprites, binary, etc.)
- **Freespace manager**: ROM space allocation for modified assets
- **Extraction presets**: JSON config mapping ROM addresses → asset types

### DiztinGUIsh (`/mnt/crucial/projects/DiztinGUIsh/`)
- **SNES disassembler**: Per-byte annotation (opcode/operand/data classification)
- **Tracelog capture**: Real-time from BSNES+/Mesen2 via socket (port 27015)
- **Format**: .diz (gzip XML) / .dizraw (plain XML) — Git-friendly
- **Assembly export**: Asar-compatible output with labels, comments, register state
- **ROM mapping**: All modes (LoROM, HiROM, SA-1, SuperFX, ExHiROM, ExLoROM)
- **Auto-stepper**: Follows execution paths, detects desync

### Key Cross-Project Patterns

| Pattern | lm3 | rbshura | zamn | tile_ext | Overlap |
|---------|-----|---------|------|----------|---------|
| SNES addr math | LoROM | HiROM | LoROM | — | **Unify** |
| LZSS decomp | — | Custom | ZAMN variant | — | **Parameterize** |
| 2BPP tiles | — | Yes | — | — | **Merge** |
| 4BPP tiles | — | Yes | Yes | — | **Merge** |
| .tbl parser | Yes | Yes | — | — | **Unify** |
| Mesen IPC | Yes | Yes | Yes (docs) | — | **Single client** |
| Text extraction | Pointer-based | FF-delimited | — | — | **Both patterns** |
| Font pipeline | 1BPP-IL VWF | 2BPP 16x16 | — | — | **Generalize** |
| Godot export | — | — | .tscn/.tres | .tres/.tscn | **Merge** |
| Tile classify | — | — | — | K-means | **Integrate** |

---

## Proposed Architecture

```
retrotool/                            # repo: SFCRetroTools (github.com/danielburgess/SFCRetroTools)
├── pyproject.toml                    # Python 3.12+, modern packaging
├── retrotool/                        # package dir (flat layout, matches v0.1)
│   └── ...
│   ├── __init__.py
│   ├── cli.py                        # Click/Typer CLI entrypoint
│   │
│   ├── core/                         # Platform-agnostic primitives
│   │   ├── rom.py                    # ROM loading, header detection, mapping type
│   │   ├── address.py                # SNES addr math (from retrotool/snes.py)
│   │   ├── pointer.py                # Pointer table R/W (8/16/24-bit)
│   │   ├── binary.py                 # Binary read/write helpers, struct utils
│   │   └── cache.py                  # SHA-256 build caching
│   │
│   ├── project/                      # Project system
│   │   ├── schema.py                 # Project file schema (TOML/YAML)
│   │   ├── loader.py                 # Load/validate project definitions
│   │   ├── datadef.py                # Data structure definition parser
│   │   └── templates/                # Project scaffolding templates
│   │
│   ├── graphics/                     # Graphics extraction
│   │   ├── tiles.py                  # 2BPP/4BPP/8BPP planar decode/encode
│   │   ├── palette.py                # BGR555/RGB palette handling
│   │   ├── sprites.py                # Sprite composition, atlas generation
│   │   ├── animation.py              # Animation sequence extraction
│   │   ├── tilemap.py                # Tilemap/metatile rendering
│   │   └── font.py                   # Font extract/build (1BPP-IL, VWF widths)
│   │
│   ├── compression/                  # Compression framework
│   │   ├── base.py                   # ABC for compressor/decompressor
│   │   ├── lzss.py                   # LZSS variants (parameterized)
│   │   ├── rle.py                    # RLE variants
│   │   ├── huffman.py                # Huffman coding
│   │   ├── lz_nintendo.py            # Nintendo LZ (GBA/SNES common)
│   │   ├── detector.py               # Heuristic compression detection
│   │   └── registry.py               # Named compression scheme registry
│   │
│   ├── script/                       # Text/script system
│   │   ├── table.py                  # .tbl parser + longest-match codec
│   │   ├── extractor.py              # Script extraction from ROM
│   │   ├── inserter.py               # Script insertion + pointer update
│   │   ├── dte.py                    # DTE overflow system
│   │   └── validator.py              # Round-trip + structural validation
│   │
│   ├── heuristics/                   # Data structure discovery
│   │   ├── pointers.py               # Pointer table scanner (from snes_ptr_finder)
│   │   ├── text.py                   # Text block detection
│   │   ├── graphics.py               # GFX data signature detection
│   │   ├── compression.py            # Compressed block finder
│   │   ├── structures.py             # Generic struct pattern matching
│   │   └── mapper.py                 # ROM region classification
│   │
│   ├── debugger/                     # Mesen-Diz IPC integration
│   │   ├── client.py                 # IPC client (expand mesen_ipc.py)
│   │   ├── automation.py             # Automated testing sequences
│   │   ├── tracer.py                 # Execution tracing + analysis
│   │   ├── memory_watch.py           # Memory diff/watch patterns
│   │   ├── breakpoint_manager.py     # Smart breakpoint strategies
│   │   ├── input_playback.py         # Controller input automation
│   │   └── discovery.py              # Auto-discovery workflows
│   │
│   ├── asm/                          # Assembly patch system
│   │   ├── codegen.py                # Python/pseudocode → 65816 ASM
│   │   ├── patcher.py                # Asar integration (build + apply)
│   │   ├── templates.py              # Common patch templates (hooks, redirects)
│   │   └── freespace.py              # ROM freespace manager
│   │
│   ├── extraction/                   # Game → modern engine extraction
│   │   ├── level.py                  # Level map extraction (collision, triggers, logic)
│   │   ├── entity.py                 # Entity/object registry builder
│   │   ├── behavior.py               # Behavior/AI extraction from disasm
│   │   └── pipeline.py               # Multi-stage extraction orchestrator
│   │
│   ├── export/                       # Export backends
│   │   ├── godot/                    # Godot 4 export
│   │   │   ├── scene.py              # .tscn generation
│   │   │   ├── resource.py           # .tres generation
│   │   │   ├── tileset.py            # TileSet w/ collision physics layers
│   │   │   ├── spriteframes.py       # SpriteFrames from animations
│   │   │   └── project.py            # project.godot scaffolding
│   │   ├── tiled/                    # Tiled editor export
│   │   │   ├── tmx.py                # .tmx tilemap generation
│   │   │   ├── tsx.py                # .tsx tileset generation
│   │   │   └── collision.py          # Object layers for collision/triggers
│   │   ├── cpp/                      # C++ data export
│   │   │   └── structs.py            # ROM structs → C++ headers/source
│   │   └── python/                   # Python data export
│   │       └── dataclasses.py        # ROM structs → Python dataclasses
│   │
│   └── ai/                           # AI-assisted mode
│       ├── prompts.py                # Prompt templates for RE workflows
│       ├── context.py                # Build context from project state
│       ├── workflows.py              # Guided RE workflows (compression ID, etc.)
│       └── ipc_prompt.py             # Generate IPC command sequences
│
├── tests/
├── examples/                         # Example projects
│   └── lm3/                          # LM3 as reference project
└── docs/
```

---

## Project File Format

### `project.toml` — Central Definition

```toml
[rom]
name = "Little Master III"
file = "lm3.sfc"
mapping = "lorom"           # lorom|hirom|exlorom|exhirom|sa1
size = "2M"                 # original size
expanded_size = "4M"        # patched size
header = false              # SMC header present?

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
pipe_name = "auto"          # auto-derive from ROM name
```

### Per-Table Data Definition (`scripts/main_dialog.toml`)

```toml
[table]
name = "main-dialog"
type = "pointer"            # pointer|fixed|dte|bytecode

[encoding]
table_file = "tables/eng.tbl"
fallback = "tables/jap.tbl"
terminator = 0x00

[pointers]
address = "$1B:8000"        # pointer table SNES addr
count = 512
size = 2                    # bytes per pointer (2 or 3)
bank_override = "$1B"       # for 2-byte ptrs

[data]
start = "$1B:8400"
compression = "none"        # none|lzss|rle|custom

[relocation]                # optional: move to expanded ROM
target = "$C1:8000"
pointer_size = 3            # upgrade to 3-byte ptrs

[display]
word_wrap = { width = 26, lines = 6 }
windowed = true
```

---

## LZSS Compression Variants (Key Finding)

Both ZAMN + RBShura use LZSS with same core algorithm but different parameters:

| Parameter | ZAMN | RBShura | Parameterized |
|-----------|------|---------|---------------|
| Window size | 4096 (12-bit) | 4096 (12-bit) | `window_bits=12` |
| Init fill byte | 0x20 (space) | 0x00 | `fill_byte=0x20` |
| Init write pos | 0xFEE | 0xFEE | `init_pos=0xFEE` |
| Min match | 3 | 3 | `min_match=3` |
| Max match | 18 | 18 | `max_match=18` |
| Size header | 16-bit LE, bit15=chain | 16-bit LE | `chained=bool` |
| Ctrl bit order | LSB-first | MSB-first | `bit_order='lsb'/'msb'` |
| Back-ref format | `[lo8, (hi4<<4)\|len]` | `[lo8, (hi4<<4)\|len]` | Same |

→ Single `LZSSCodec(params)` class handles both. Games define their params in project config.

**Necrofy** (C#) has third impl — same algo, confirms parameters for ZAMN.

**Discovery workflow**: Play game in Mesen → breakpoint on DecompressData routine → read params from registers → auto-generate codec config.

---

## Level Map Extraction (w/ Collision, Triggers, Logic)

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

1. Read collision table from ROM (per-tileset, typically 512 entries × 2 bytes)
2. Map collision type flags → semantic categories
3. Generate per-metatile collision shapes (combining 8×8 tile collision into 64×64 metatile)
4. Export as physics polygons (Godot) or object rectangles (Tiled)
5. Trigger detection via heuristic: scan for warp/transition pointer tables near level data

---

## Implementation Phases

| Phase | Scope | Priority | Status (2026-04-13) |
|-------|-------|----------|---------------------|
| **1** | `core/` — ROM, address, pointer, binary, cache | **Foundation** | ✅ Done + tested |
| **2** | `project/` — schema, loader, datadef | **Foundation** | ✅ Done + tested |
| **3** | `graphics/` — tiles, palette, sprites, tilemap | High | 🟡 Partial — font.py + animation.py missing |
| **4** | `compression/` — framework + LZSS + detector | High | 🟡 Partial — LZSS/RLE done; huffman + lz_nintendo missing |
| **5** | `script/` — table, extractor, inserter, dte, validator | High | 🟡 Partial — only `type=pointer` extractor; inserter doesn't patch ROM yet |
| **6** | `debugger/` — IPC client, tracer, automation | High | 🟡 Scaffold — untested vs live Mesen; tracer/input_playback/discovery missing |
| **7** | `heuristics/` — pointer/text/gfx/mapper | Medium | 🟡 Partial — compression-heuristic + structures modules missing |
| **8** | `asm/` — codegen, patcher, freespace, templates | Medium | ✅ Done + smoke-tested (asar CLI path only; no Python bindings yet) |
| **9** | `extraction/` — Level/Entity/Behavior models + Pipeline | Medium | 🟡 Models only — no game-specific extraction logic |
| **10** | `export/godot/` + `export/tiled/` | Medium | ✅ Emitters produce valid text; not yet round-tripped through Godot 4/Tiled |
| **11** | `export/cpp/` + `export/python/` | Lower | ✅ Done + smoke-tested |
| **12** | `ai/` — prompts, workflows, context builder | Lower | ✅ Scaffolded |

### Completed pieces (smoke-tested in session)
- SFCAddress roundtrip across all mapping modes
- BuildCache put/get/invalidate + SHA-256 helpers
- ROM SMC-header strip + internal header detection (checksum-complement XOR scoring)
- TOML project loader with `$1B:8000`, `2M`, `8K` literal parsing
- DataDef loader for pointer tables w/ relocation sections
- Palette BGR555↔RGB888 (bit-replicated)
- Tile 1/2/4/8 BPP planar codec roundtrip, flip, grid composite
- Tilemap entry 16-bit word roundtrip (tile/pal/pri/hflip/vflip)
- Sprite render_frame + atlas row-packer
- LZSSCodec three presets (RBShura / ZAMN / legacy) roundtrip on multiple cases
- LZSS chain decompression with callback for pointer resolution
- RLE ctrl-byte codec roundtrip
- Compression scheme registry with `lzss`, `lzss-rbshura`, `lzss-zamn`, `lzss-legacy`, `rle`
- Table encode_text longest-match with `[HH]` literal escape
- Script extractor end-to-end: ROM bytes → pointer table → decoded strings
- DTE digraph mining + savings estimation
- Mesen IPC client class + derive_pipe_name sanitizer (wire format only)
- Heuristic pointer-table scanner with monotonic-fraction scoring
- Heuristic text scanner with configurable terminators
- Heuristic graphics scanner (Shannon entropy + plane-pair correlation)
- Region merge + gap fill
- FreeSpace allocator with alignment + coalesce
- AsmBuilder fluent codegen
- Asar patcher with BuildCache-keyed skip (key = sha256 rom + asm + includes + defines)
- Godot `.tscn` / `.tres` / TileSet / SpriteFrames text emitters
- Tiled `.tmx` (layers + object group for triggers/spawns) + `.tsx` tileset XML
- C++ struct header emitter w/ namespace + include guard
- Python dataclass module emitter
- AI prompt templates + ipc_prompt.IpcPlan JSON output

### Preexisting bugs fixed during port
- `SFCPointer(low, high, bank=None)` crashed because `None` fell through `validate_bytes` → `integer_or_hex`. Patched to normalize `None` → `0`.

---

## Remaining Work

### Phase 13 — CLI

Single entrypoint via Typer (or Click). Subcommands match module boundaries.

```
retrotool addr <snes_or_pc> [--mapping lorom|hirom|...]
retrotool rom info <rom_file>
retrotool project init <dir> [--rom <file>] [--mapping <mode>]
retrotool project status [--project <path>]
retrotool script extract <datadef>
retrotool script insert <datadef> <texts_file>
retrotool script validate <datadef>
retrotool gfx extract <rom> --offset <addr> --bpp <2|4|8> --tiles <N> --palette <addr>
retrotool gfx palette <rom> --offset <addr> [--count 16]
retrotool lzss decompress <rom> --offset <addr> --preset <rbshura|zamn|legacy>
retrotool lzss compress <file> --preset <...>
retrotool heur pointers <rom> --entry-size 2|3 --bank <HH>
retrotool heur text <rom>
retrotool heur gfx <rom>
retrotool heur compression <rom> --presets <list>
retrotool debug connect [--pipe <name>]
retrotool debug read <memtype> <addr> <len>
retrotool debug watch <memtype> <addr> <len> --ticks <N>
retrotool asm build <project> [--cache]
retrotool export godot <project> --out <dir>
retrotool export tiled <project> --out <dir>
```

Status: not started.

### Phase 14 — Reference Projects (`examples/`)

Concrete, runnable examples exercising the full stack end-to-end.

- `examples/lm3/` — Little Master III
  - `project.toml`, `tables/`, `scripts/` data definitions
  - CLI-driven extract → edit → compile → asar patch → round-trip validate
  - Targets: reproduce lm3 translation pipeline using retrotool v2 instead of vendored copy
- `examples/rbshura/` — Rushing Beat Shura
  - HiROM project w/ LZSS-RBShura preset, FF-delimited text, 2BPP 16x16 font
  - Font pipeline port of `build_en_font.py` (1574 lines → retrotool.graphics.font)
- `examples/zamn/` — Zombies Ate My Neighbors
  - LZSS-ZAMN preset w/ chain blocks
  - Level extraction → Godot 4 scene export (collision + triggers + spawns)
- `examples/minimal/` — Smallest possible project
  - Synthetic 32KB ROM, one pointer table, one string table
  - Meant as integration-test fixture and onboarding walkthrough

Status: not started.

### Phase 15 — GUI Shell

Goal: desktop app that hosts retrotool operations with game-specific editor panels. Two reasonable implementations:
- **PySide6 (Qt)** — mature, rich widget set, good table/hex editing, supports embedded docks.
- **pywebview + local HTTP** — reuses the translation-preview pattern from rbshura; friendlier for custom visual editors (CSS/canvas), but slower for large tables.

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
- **Heuristic runner** — one-click scans against current ROM and/or a captured debugger state; results go into a findings pane that can promote candidates into `project.toml` datadefs.
- **Asar build panel** — triggers `asm.apply_patch`, shows cache hits, tailed build log.
- **Diff panel** — compare current ROM vs original baseline, filtered by region classification.

Status: not started. Likely a new `retrotool-gui` sibling package depending on `retrotool` core, to keep the library dep-free.

### Phase 16 — Runtime-Guided Heuristics

Ratchet up detection accuracy by combining static ROM heuristics with live debugger state.

- **Pointer-table discovery via memory access patterns**
  - Set a write breakpoint on a suspected text buffer / tilemap buffer
  - Capture source PC + operand ROM address on each write
  - Cluster operand addresses by stride → infer pointer-table base, entry size, and length
  - Walk back from the hit PC to the source register and find the table origin
- **Data block localization by DMA trace**
  - Tap DMA/HDMA register writes via breakpoint on registers $4300-$437F
  - For each transfer, record (source_bank:addr, dest, size) tuples
  - Classify destinations: VRAM → graphics, CGRAM → palette, OAM → sprites, WRAM → working data
  - Feed back into `heuristics.mapper` as high-confidence regions
- **Text system discovery by glyph-write correlation**
  - Put write breakpoint on tilemap VRAM range corresponding to text window
  - Intercept source byte + glyph tile index written per call
  - Auto-derive `.tbl` mapping: observed_byte → glyph_tile → ASCII char (via human-in-the-loop glyph labeling or OCR)
- **Compression scheme fingerprinting**
  - Breakpoint on reset vector's early copy routines
  - Detect ring-buffer writes (repeated 0x1000-byte-modular writes with 0xFEE start) → LZSS family
  - Extract fill byte by observing initial ring state (`$0000-$0FFF` WRAM after reset but before decompress)
  - Present confirm/reject to user, register as named preset in project
- **Behavior/state-machine extraction**
  - Tracelog-based: record (PC, A, X, Y, P, DB, PB) per instruction for N frames
  - Build control-flow graph of entity handler; identify loops and branches that correspond to AI states
  - Emit skeleton `Behavior` dataclass with state stubs for manual annotation
- **Coverage-driven classification**
  - Use Mesen CDL (code/data logger) export via IPC to separate executed code from unreached data
  - Refine `Region` classifications — `executed` overrides `unknown` / `code`
- **Playthrough-derived discovery sessions**
  - Record an input playback, walk through level transitions, save-state each transition
  - Diff memory between save-states to locate per-level data pointers
  - Correlate save-state PCs with pointer-table candidates to auto-bind levels ↔ data

Status: not started. Depends on Phase 6 hardening (live IPC validation) and Phase 15 GUI integration for human-in-the-loop confirmation.

### Back-compat + Migration (outstanding from original plan)
- Merge additions from `sfc-lm3-eng/retrotool/` vendored-diverged copy back into v.9
- Migrate `sfc-rbshura` from local `rbshura.py` to `retrotool.compression.lzss-rbshura` + `retrotool.script.Table`

### Test suite (cross-cutting)
- `tests/` — pytest. Target ≥80% coverage on `core`, `compression`, `graphics`, `script`.
- Golden-file tests for Godot/Tiled/C++/Python emitters.
- Live-IPC smoke tests behind a `--mesen` marker (skip by default).

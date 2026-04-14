# retrotool

**SNES/SFC ROM hacking toolkit** — v0.8.0

A Python library that consolidates the tooling scattered across multiple ROM-hacking projects
into a single installable package: address math, ROM header handling, tile/palette/sprite codecs,
compression (LZSS + RLE), script dumping/insertion, pointer-table heuristics, Mesen2-Diz
debugger IPC, asar patching, and Godot/Tiled/C++/Python export emitters.

v0.8 is the first published version of the post-rewrite scope. The 0.1 line (address-only)
still works through compatibility shims; 1.0 will land after examples + CLI + test suite.

## Install

```
pip install retrotool
```

Requires Python 3.12+ (uses stdlib `tomllib`).

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
- `SpriteFrame` / `render_frame` — compose frames from positioned 8×8 tiles; `pack_atlas`
  returns an `Atlas` with per-frame `AtlasEntry` origin metadata.

Not yet in v0.8: `font.py` (1BPP-IL VWF + 2BPP 16x16 glyph pipelines), `animation.py`.

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

Not yet in v0.8: Huffman, Nintendo LZ77.

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

Untested against a live Mesen process in v0.8 — wire format is implemented per the
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
LLM-assisted reverse-engineering scaffolding.

- `prompts.*` — templates for compression identification, text-table location, level-format
  discovery, asar hook generation.
- `workflows.*` — ordered `WorkflowStep[]` sequences.
- `ipc_prompt.IpcPlan` — structured Mesen-IPC command sequence an LLM can fill in and a
  `MesenClient` can consume directly.
- `build_context(project, ...)` — project → prompt prelude.

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

- **0.8** (current) — 12 library modules scaffolded, core paths smoke-tested.
- **0.9** — CLI (`retrotool …` subcommands) + example projects (lm3, rbshura, zamn, minimal)
  + pytest suite.
- **1.0** — GUI shell with project explorer, game-specific script editor, graphics extractor,
  built-in hex editor polling the debugger, pointer-table inspector, asar build panel; and
  runtime-guided heuristics that combine static scans with live Mesen state (write-breakpoint
  pointer discovery, DMA-trace data localization, glyph-correlation text discovery, LZSS
  fingerprinting via ring-buffer detection).

## License

See [LICENSE](./LICENSE).

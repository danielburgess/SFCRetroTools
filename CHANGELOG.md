# Changelog

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

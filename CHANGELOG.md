# Changelog

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

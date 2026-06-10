# Translation project quickstart

A **complete, runnable retrotool translation project**. It translates a tiny
synthesized "demo game" from Japanese to English using the exact same workflow,
config files, and commands a real SNES fan translation uses — so you can run the
whole loop in two minutes, then copy this directory as the starting point for a
real project.

```
translation-project/
├── project.toml          # the project: ROM, languages, build, editor config
├── defs/
│   └── dialog.toml       # DataDef: where the dialog pointer table lives + encoding
├── tables/
│   ├── game_jp.tbl       # byte <-> kana table (the game's original encoding)
│   └── game_en.tbl       # byte <-> ASCII table (the translation's encoding)
├── data/
│   ├── jp/dialog.txt     # extracted original script (read-only reference)
│   └── en/dialog.txt     # the English translation (edit this!)
├── tools/
│   └── make_demo_rom.py  # synthesizes roms/game.sfc (stand-in for a real ROM)
├── scripts/              # contributor environment setup (Linux/macOS + Windows)
└── roms/, out/           # source ROM in, built ROM out (both gitignored)
```

Every config file is heavily commented — `project.toml` and `defs/dialog.toml`
are the reference reading.

## Run the whole loop

```bash
# 0. One-time environment setup (installs uv, .venv, retrotool[all,editor]):
scripts/setup.sh                      # Windows: scripts\win\setup.cmd
# …or if you already have retrotool installed, skip straight ahead.

# 1. Make the demo "game" ROM (a real project: copy your legally-owned ROM here)
python tools/make_demo_rom.py         # -> roms/game.sfc

# 2. Build the translated ROM: encodes data/en/dialog.txt with tables/game_en.tbl,
#    re-packs the strings, rewrites the game's pointer table
retrotool build .                     # -> out/demo_en.sfc

# 3. Edit the translation in the GUI (entries, byte counts, overflow warnings,
#    JP reference column) — then build again
retrotool edit .
```

Check the result: `out/demo_en.sfc` has `Hello,[FD]world!`, `Fight!`, `The End`
re-packed after the pointer table at PC `0x4000`, with the three 16-bit pointers
rewritten to match. The original Japanese bytes are left in place — relocate mode
never destroys data it doesn't have to touch.

## What each piece does

**`project.toml`** declares the languages (`jp_data_dir` / `en_data_dir`), which
one the build inserts (`build_lang = "en"`), the source ROM (`[rom]`), and where
DataDefs live (`data_dirs = ["defs"]`).

**`defs/dialog.toml`** is a *DataDef* — one block of game data, declaratively:
the pointer table's PC offset / entry count / pointer width, the encoding table
and terminator byte, and how the build places the translation (`relocate`).
A real game has one DataDef per script block (dialogs, menus, item names, …);
they're discovered automatically from `data_dirs`.

**`tables/*.tbl`** map bytes to characters (`80=こ`, `41=A`). The JP table
mirrors the game's original font encoding; the EN table is what your inserted
font uses (here: ASCII identity). Control bytes (line break `FD`, terminator
`FF`) are opcodes, not characters — they stay out of the tables and appear in
script files as `[FD]` escapes.

**`data/<lang>/dialog.txt`** is the script in retrotool's dump format — UTF-16,
one `<<$ADDR:idx[$PTR]>>` header per entry, control codes as `[XX]`:

```
<<$16384:0[$16448]>>
Hello,[FD]world!
```

Translate the body lines; never touch the headers.

## How `data/jp/` was made (and how to redo it)

The JP reference dump comes from the ROM itself. Extraction decodes with
whatever table the DataDef points at, so point it at the JP table for this
one step (this is also exactly what you'd do first in a brand-new project —
extract the original script before translating anything):

```bash
# defs/dialog.toml: table_file = "tables/game_jp.tbl"   (temporarily)
retrotool extract . --lang jp -y      # -> data/jp/dialog.txt
# restore: table_file = "tables/game_en.tbl"
```

`[extract].default_lang = "jp"` in project.toml makes plain
`retrotool extract .` target the *source* dump on purpose — extraction should
never silently overwrite your translation in `data/en/`.

## Starting a third language

```bash
retrotool lang new --from en --to fr
```

One command stages everything: copies `data/en` → `data/fr`, declares
`fr_data_dir`, sets `build_lang = "fr"`, renames the output ROM
(`demo_en` → `demo_fr`), forks `tables/game_en.tbl` → `tables/game_fr.tbl`,
and repoints `defs/dialog.toml` at the fork — printing the full plan and
asking before writing. Then translate `data/fr/` and build.

## Adapting this to a real game

1. Copy this directory; replace `roms/game.sfc` with your legally-obtained ROM
   (keep it gitignored — never distribute it) and fix `[rom]`
   (`mapping`, `size`, `name`).
2. Find the script's pointer table (Mesen2 debugger + retrotool's
   `retrotool.heuristics.pointers` scanner help here) and describe it in a
   DataDef. Repeat per script block.
3. Build the original-encoding `.tbl` (or take one from the community —
   table files are a ROM-hacking standard), extract, build your EN table to
   match your inserted font, translate, build.
4. The demo doesn't need them, but real projects usually add: `[editor]`
   preview config (game font + palette table) for pixel-accurate previews,
   `[rom.build].freespace` ranges once strings outgrow their banks,
   `kind = "asar"` sections for engine patches (VWF, font renderers), and
   `kind = "graphics"` sections for translated art. See the rbshura project
   and the main README's CLI Reference for all of it.

The `scripts/` folder (one-shot `uv`-based environment setup with a printed
✓/✗ checklist, Linux/macOS + Windows) is adapted from the Rushing Beat Shura
translation's contributor tooling and is generic — it reads the ROM path from
`project.toml`, so it works unchanged in your copy.

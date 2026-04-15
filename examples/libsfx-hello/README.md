# libsfx-hello

End-to-end walkthrough: scaffold a libSFX project, build a ROM from pure
Python, then drive the same build through an MBXML spec with an extra
byte-patch on top.

Requires: `pip install 'retrotool[libsfx]'` (installs the `retrotool-libsfx`
wheel with the bundled ca65/ld65/libSFX toolchain).

## 1. Scaffold from the bundled Template

```bash
retrotool libsfx scaffold ./demo --template Template
```

This copies `libSFX/examples/Template/` into `./demo/` and writes a minimal
`project.toml`:

```toml
[build.libsfx]
name = "demo"
debug = 0
```

## 2. Build directly

```bash
retrotool libsfx build ./demo --debug 2 -o demo.sfc
# → demo.sfc, demo.sym, demo.map, demo.dbg, demo.bp
```

`debug=2` asks ld65 for sym+map+dbg files and triggers Mesen breakpoint
generation. `debug=0` is silent-runtime; `debug=1` is sym+bp only.

Or from Python:

```python
from pathlib import Path
from retrotool.asm.libsfx import LibSFXProject

project = LibSFXProject.discover(Path("demo"))
project.cfg.debug = 2
result = project.build()

print("rom:      ", result.rom)
print("sym:      ", result.symfile)
print("map:      ", result.mapfile)
print("dbg:      ", result.dbgfile)
print("bp:       ", result.breakpoints)
print("checksum: ", f"{result.header.checksum:04X}")
print("took:     ", result.duration_ms, "ms")
```

## 3. Drive it from MBXML

`hello.mbxml` embeds the same libSFX build as an MBXML section and patches
four bytes into the linked ROM:

```xml
<build name="demo-patched">
  <libsfx src="./demo" debug="0"/>
  <rep file="patch.bin" offset="10"/>
</build>
```

```bash
printf '\xDE\xAD\xBE\xEF' > patch.bin
retrotool build hello.mbxml -o demo-patched.sfc
```

Because a `<libsfx>` section provides the ROM canvas, `<build original=>`
is optional. Any `<rep>/<ins>/<bin>/<asar>` after the `<libsfx>` patches the
just-linked ROM in declaration order.

## 4. Load in Mesen

Open `demo.sfc` in Mesen2; if you built with `debug>=1`, load the sibling
`.bp` file for labelled breakpoints. Paired with `retrotool.debugger.MesenClient`
over Mesen's Lua IPC you can step the ROM from Python.

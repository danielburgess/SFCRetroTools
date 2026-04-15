# libSFX Integration — Plan

Integrate [Optiroc/libSFX](https://github.com/Optiroc/libSFX) (MIT, 65816 asm framework on top of ca65) into retrotool. Sibling effort to SuperFamiconv bundling — same upstream author, same packaging playbook.

**Target:** retrotool 0.9.x (alongside MBXML extensions + compression-engine rework).
**Non-goals:** porting libSFX code to Python, replacing ca65 with asar.

---

## 1. Scope

libSFX is fundamentally a **toolchain** (ca65/ld65 + helper tools) plus an **asm runtime** (`include/`). Two integration surfaces:

### A. Runtime (data-only, pure asm)
`include/` tree is plain text asm — can ship as-is via a wheel, no build step needed.

### B. Toolchain (binaries)
ca65, ld65, superfamicheck, brrtools, lz4, make_breakpoints — all C/C++ programs built via `make`. SuperFamiconv already handled (our subpackage). Remaining four need the same treatment.

---

## 2. Package layout

Mirror SuperFamiconv pattern:

```
packages/
  retrotool-superfamiconv/   # existing
  retrotool-ca65/            # new: ships ca65 + ld65 + cc65 binaries
  retrotool-sneshelpers/     # new: superfamicheck + brrtools + lz4 + make_breakpoints
  retrotool-libsfx/          # new: the include/ tree + Configurations/ + Packages/
```

Rationale for splitting: users who want libSFX need **all four** wheels; but ca65 alone (a popular 6502 assembler) is useful standalone, and sneshelpers (brrtools, superfamicheck) are useful without libSFX too. Separate packages keep each individually installable.

Main-package extra:

```toml
[project.optional-dependencies]
graphics = ["retrotool-superfamiconv>=0.1.0"]
asm-ca65 = ["retrotool-ca65>=0.1.0"]
libsfx = [
  "retrotool-ca65>=0.1.0",
  "retrotool-sneshelpers>=0.1.0",
  "retrotool-libsfx>=0.1.0",
  "retrotool-superfamiconv>=0.1.0",
]
```

`pip install retrotool[libsfx]` pulls the whole chain. Each subpackage uses cibuildwheel; libSFX include/ ships as a pure-python `py3-none-any` wheel (data files only).

---

## 3. Retrotool-side API

New module `retrotool.asm.ca65`:

```python
class Ca65Assembler:
    def __init__(self, include_dirs: list[Path] = None, defines: dict[str, str] = None): ...
    def assemble(self, src: Path, out_obj: Path) -> AsmResult: ...

class Ld65Linker:
    def __init__(self, config: Path, cfg_path: list[Path] = None): ...
    def link(self, objs: list[Path], out_rom: Path, *, debug: int = 0) -> LinkResult: ...
```

New module `retrotool.asm.libsfx`:

```python
def libsfx_include_dir() -> Path:
    """Root of shipped libSFX include/ tree."""

def default_config() -> Path:
    """Configurations/libSFX.cfg"""

def default_map() -> Path:
    """Configurations/Map.cfg"""

class LibSFXProject:
    """Mirrors libSFX.make's per-project build.

    Resolves __STACKSIZE__, __ZPADSIZE__, __ZNMISIZE__, __RPADSIZE__,
    optional __DEBUG__=1. Dispatches .s (CPU) / .s700 (SMP) / .sgs (GSU)
    to ca65 with correct TARGET_* defines.
    """
    def __init__(self, root: Path, *, debug: int = 0,
                 stack_size=0x100, zp_pad_size=0, znmi_size=0, rp_size=0): ...
    def sources(self) -> list[Path]: ...
    def build(self, out: Path) -> BuildResult: ...   # → .sfc
```

New module `retrotool.build.libsfx_project`:

- `scaffold_libsfx_project(dest, template="Template")` — copy one of libSFX's `examples/Template` or `Template-Mode21` trees to a fresh dir, inject project.toml.
- MBXML extension element `<libsfx>` — point at a libSFX source tree and have retrotool build it as a pipeline stage (feeds the resulting `.sfc` into subsequent `<rep>`/`<ins>` sections).

---

## 4. Phases & milestones

**Phase 1 — retrotool-libsfx (pure-python include-tree wheel)** (1 day)
- Submodule `vendor/libSFX`.
- Subpackage `packages/retrotool-libsfx/` ships `include/` + `Configurations/` + `Packages/` as data files.
- API: `libsfx_include_dir()`, `default_config()`, `default_map()`, `example_path(name)`.
- No native build needed — plain `py3-none-any` wheel.

**Phase 2 — retrotool-ca65 (ca65 + ld65 binaries)** (1–2 days)
- Submodule `vendor/cc65` (upstream cc65 — used by libSFX unmodified).
- `setup.py` runs `make` in the submodule, grabs `bin/ca65`, `bin/ld65`, `bin/cc65`, `bin/co65` into `retrotool_ca65/bin/`.
- cibuildwheel matrix same as SuperFamiconv.
- API: `ca65_binary()`, `ld65_binary()`, subprocess helpers.

**Phase 3 — retrotool-sneshelpers (superfamicheck + brrtools + lz4 + make_breakpoints)** (1–2 days)
- Four submodules, same build pattern.
- API: `superfamicheck_binary()`, `brrtools_binary()`, `lz4_binary()`, `make_breakpoints_binary()`, plus high-level wrappers:
  - `fix_rom_header(sfc_path, pad=False)` — superfamicheck shim.
  - `encode_brr(wav, *, loop=None, resample_rate=1.0, gauss_filter=True)` — brr_encoder.
  - `compress_lz4(data) -> bytes` — lz4 CLI.

**Phase 4 — retrotool.asm.ca65 module** (1 day)
- `Ca65Assembler` + `Ld65Linker` classes calling the retrotool-ca65 binaries via subprocess.
- Cache integration (hash of src + includes + defines → skip reassembly if cached).
- Unit tests: assemble a stub `.s`, link, verify output exists and has correct size.

**Phase 5 — retrotool.asm.libsfx module** (1–2 days)
- `LibSFXProject` class replicating `libSFX.make` logic in Python.
- Source discovery (glob for `.s`, `.s700`, `.sgs`).
- Compile → link → superfamicheck post-process chain.
- Debug modes 0/1/2 (symbol/map/dbgfile emission).
- Scaffolder: copy a libSFX example template into a user project dir.

**Phase 6 — MBXML extension + CLI** (0.5 day)
- `<libsfx src="./game_src" debug="2"/>` element in MBXML → runs `LibSFXProject.build()` mid-pipeline, drops the `.sfc` where later sections can pick it up.
- `retrotool libsfx scaffold <name> [--template=Template-Mode21]` CLI subcommand.
- `retrotool libsfx build <dir>` CLI subcommand.

**Phase 7 — Docs + CHANGELOG + ship in 0.9.x**
- README section on libSFX usage.
- Sample walkthrough: scaffold → add a tile → build → run in Mesen via `retrotool.debugger`.
- CHANGELOG entry.

Total estimate: ~7 workdays for full scope. Phase 1 alone is useful (lets users reference the include tree without build hassle).

---

## 5. Interactions with other 0.9 goals

- **Compression rework**: libSFX uses lz4 externally (via `lz4 -f -9`). When retrotool grows its own LZ4 implementation, `sneshelpers` can expose both the binary AND the pure-python codec, user picks.
- **MBXML extensions**: `<libsfx>` element fits cleanly into MBuild-style sequential pipeline — emit ROM first, then patch with `<rep>`/`<ins>`/`<asar>` sections.
- **retrotool.debugger**: libSFX's `make_breakpoints` tool emits Mesen-compatible symbol files from ld65 output. Wire that into `MesenClient.load_breakpoints(sym_file)` for seamless debugging of libSFX projects.
- **retrotool.project**: Add `[build.libsfx]` section to project.toml so users don't need MBXML for simple libSFX projects — the toml-driven path works end to end.

---

## 6. Open questions

1. Does cc65 / ca65 already have pypi packaging upstream? If yes, prefer to depend on that rather than rebuild. (Need to check — likely no, cc65 is C toolchain.)
2. libSFX pins cc65 to a specific submodule commit. Do we follow that pin, or track cc65 upstream? Follow libSFX's pin for compatibility guarantees.
3. Windows build: cc65 + brrtools on MinGW should work, but brrtools has historically been fussy. Budget an extra day for Windows CI debug.
4. ARM macOS: cc65 builds cleanly on arm64 as of recent versions. Should be fine.
5. Licensing: libSFX MIT, cc65 ZLib, brrtools BSD, lz4 BSD-2-Clause, superfamicheck MIT, make_breakpoints unclear — verify before shipping. Collect LICENSE files into wheel metadata.

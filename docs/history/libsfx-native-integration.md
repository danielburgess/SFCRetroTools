# libSFX native integration — retrotool 0.9.0 release plan

Goal: retrotool can scaffold, build, assemble, link, post-process, compress, and debug a libSFX project end-to-end in pure Python, with zero user-facing subprocess calls. Packaging (retrotool-libsfx wheel) is already working — this plan covers the retrotool-side ergonomic layer.

**Release gate:** 0.9.0 does not ship until every phase below is green.

---

## Architectural decisions

- **Binary resolution is centralized** in `retrotool._toolchain` (new). Every subsystem imports from there, not from `retrotool_libsfx` directly. Lets us swap in system binaries later (`RETROTOOL_USE_SYSTEM_TOOLS=1`) without touching callers.
- **One error type:** `retrotool.ToolchainError` wraps both `ImportError` (retrotool-libsfx not installed) and `ToolNotBundledError` (wheel built without a specific tool). Message always tells the user `pip install retrotool[libsfx]`.
- **Dataclass results, not tuples.** `AsmResult`, `LinkResult`, `BuildResult` with `.stdout`, `.stderr`, `.returncode`, `.artifacts: dict[str, Path]`, `.duration`.
- **Caching by content hash.** `~/.cache/retrotool/asm/<sha256>.o` — skip reassembly when src+includes+defines+tool-version all match.
- **Tests use pytest + bundled fixture ROMs/asm.** Each wrapper has a golden-path integration test + error-path unit test.

---

## Phase A — Foundation (1 day)

**Module:** `retrotool._toolchain`

```python
def ca65() -> Path: ...          # resolves via retrotool_libsfx or $PATH or error
def ld65() -> Path: ...
def superfamiconv() -> Path: ...
def superfamicheck() -> Path: ...
def brr_encoder() -> Path: ...
def brr_decoder() -> Path: ...
def lz4() -> Path: ...
def make_breakpoints() -> Path: ...
def libsfx_include() -> Path: ...
def libsfx_config() -> Path: ...
def libsfx_packages() -> Path: ...

def tool_version(name: str) -> str: ...   # runs `<bin> --version`, caches
```

Behavior: prefer bundled (`retrotool_libsfx`), fall back to `$PATH`, raise `ToolchainError` with install hint.

**Migration:** rewrite `retrotool/graphics/superfamiconv.py` to use `_toolchain.superfamiconv()`. Remove `_resolve_binary` duplicate.

**Tests:** `tests/test_toolchain.py` — verify every accessor returns a Path that exists; verify error message when pretend-uninstalled.

---

## Phase B — asm.ca65 (1.5 days)

**Module:** `retrotool.asm.ca65`

```python
@dataclass
class AsmResult:
    obj: Path
    stdout: str
    stderr: str
    duration_ms: int

@dataclass
class LinkResult:
    rom: Path
    symfile: Path | None
    mapfile: Path | None
    dbgfile: Path | None
    stdout: str; stderr: str; duration_ms: int

class Ca65Assembler:
    def __init__(self,
                 include_dirs: list[Path] = (),
                 defines: dict[str, str] = None,
                 cpu: str = "65816",
                 debug: bool = False,
                 cache_dir: Path | None = None): ...
    def assemble(self, src: Path, out_obj: Path | None = None) -> AsmResult: ...
    def assemble_many(self, srcs: list[Path]) -> list[AsmResult]: ...  # parallel

class Ld65Linker:
    def __init__(self,
                 config: Path,
                 lib_dirs: list[Path] = (),
                 debug_level: int = 0):  # 0=none,1=symfile,2=+mapfile,3=+dbgfile
        ...
    def link(self, objs: list[Path], out_rom: Path) -> LinkResult: ...
```

**Cache key:** `sha256(src_bytes + sorted(include_file_hashes) + defines + cpu + ca65_version)`.

**Tests:**
- `tests/asm/test_ca65_hello.py` — assemble a 3-line stub (`.org $8000 / nop / rts`), link against minimal cfg, assert ROM size + first byte.
- Cache hit test (run twice, second is faster + didn't touch ca65).
- Error surfacing (undefined symbol → `LinkResult` isn't returned, `Ca65Error` raised with ld65 stderr).

---

## Phase C — rom.superfamicheck (0.5 day)

**Module:** `retrotool.rom.header`

```python
def fix_rom_header(sfc: Path, *, pad: bool = False, backup: bool = False) -> dict:
    """Fix SNES header + checksum in-place via superfamicheck.
    Returns dict of before/after checksum, complement, rom_size."""
```

**Tests:** fixture ROM with bad checksum → fix → re-read checksum matches spec.

---

## Phase D — audio.brr (0.5 day)

**Module:** `retrotool.audio.brr`

```python
def encode_brr(wav: Path, out_brr: Path, *,
               loop_point: int | None = None,
               resample_rate: float = 1.0,
               gauss_filter: bool = True,
               pitch: int | None = None) -> Path:
    """Encode WAV → BRR. Returns out_brr path."""

def decode_brr(brr: Path, out_wav: Path, sample_rate: int = 32000) -> Path: ...
```

**Tests:** encode a 440Hz sine WAV → assert BRR file size ≈ expected (block count = samples/16 * 9 bytes).

---

## Phase E — compression.lz4 (0.5 day)

**Module:** `retrotool.compression.lz4_cli` (named to leave room for future pure-python impl at `retrotool.compression.lz4`)

```python
def compress_lz4(data: bytes, *, level: int = 9) -> bytes: ...
def decompress_lz4(data: bytes) -> bytes: ...
```

Temp-file pipe to lz4 binary. Mark deprecated in docstring once pure-python lands.

**Tests:** round-trip a 64KB random buffer, assert decompressed matches original; compressed fits LZ4 frame magic (`0x184D2204`).

---

## Phase F — debugger.breakpoints (0.5 day)

**Module:** `retrotool.debugger.breakpoints`

```python
def make_mesen_breakpoints(symfile: Path, out_bp: Path | None = None) -> Path:
    """Convert ca65/ld65 .sym file → Mesen breakpoint file via make_breakpoints script.
    If out_bp omitted, writes to <symfile>.bp next to symfile."""
```

**Integration with `retrotool.debugger.MesenClient`:**
```python
MesenClient.load_breakpoints(bp_file: Path)   # already exists or add
```

**Tests:** feed fixture .sym (4 labels with `;rwx` hints), assert .bp output has 4 entries with correct flags.

---

## Phase G — asm.libsfx — the big one (2 days)

**Module:** `retrotool.asm.libsfx`

```python
@dataclass
class LibSFXConfig:
    stack_size: int = 0x100
    zp_pad_size: int = 0
    znmi_size: int = 0
    rp_size: int = 0
    debug: int = 0                     # 0|1|2
    cpu_sources: list[Path] = ()       # *.s
    smp_sources: list[Path] = ()       # *.s700
    gsu_sources: list[Path] = ()       # *.sgs
    extra_includes: list[Path] = ()
    map_config: Path | None = None     # default: libsfx_config()/Map.cfg
    main_config: Path | None = None    # default: libsfx_config()/libSFX.cfg

class LibSFXProject:
    def __init__(self, root: Path, cfg: LibSFXConfig | None = None): ...

    @classmethod
    def discover(cls, root: Path) -> "LibSFXProject":
        """Glob root for .s/.s700/.sgs, read project.toml [build.libsfx] if present."""

    def sources(self) -> dict[str, list[Path]]: ...    # {"cpu": [...], "smp": [...], ...}
    def assemble(self) -> dict[Path, AsmResult]: ...   # per-source obj
    def link(self, objs: list[Path], out_rom: Path) -> LinkResult: ...
    def post_process(self, rom: Path) -> None:        # runs superfamicheck
        ...
    def build(self, out_rom: Path) -> BuildResult: ...  # full chain

@dataclass
class BuildResult:
    rom: Path
    symfile: Path | None
    breakpoints: Path | None
    checksum: int
    asm_results: dict[Path, AsmResult]
    link_result: LinkResult
    duration_ms: int

def scaffold_libsfx_project(dest: Path, template: str = "Template") -> Path:
    """Copy examples/<template> from libSFX into dest, write project.toml stub."""
```

**Target defines per source suffix** (mirroring `libSFX.make`):
- `.s` → `-D __CPU__=1 -D TARGET_CPU=1`
- `.s700` → `-D __SMP__=1 -D TARGET_SMP=1` + different cfg chain
- `.sgs` → `-D __GSU__=1 -D TARGET_GSU=1`

Plus: `-D __DEBUG__=<level>`, `-D __STACKSIZE__=<n>`, etc.

**Tests:**
- Scaffold `Template` → ~10 source files written, project.toml written.
- Build scaffolded `Template` → valid SMC ROM, passes `superfamicheck --verify`.
- Build with debug=2 → .sym + .map + .bp files emitted.
- Build is reproducible (two builds → identical SHA of ROM).

---

## Phase H — project.toml + CLI (1 day)

**Module:** `retrotool.project.libsfx` (extend existing `retrotool.project`)

```toml
# project.toml
[build.libsfx]
src = "./game_src"
stack_size = 0x200
debug = 2
map_config = "Map_Mode21_2mbit.cfg"
```

**CLI:** extend existing `retrotool` click/argparse tree.

```
retrotool libsfx scaffold <dir> [--template Template|Template-Mode21]
retrotool libsfx build [<dir>] [--debug 0|1|2] [-o out.sfc]
retrotool libsfx info <dir>       # prints discovered sources + config
retrotool libsfx clean <dir>      # wipes .build/ + cache
```

**Tests:** CLI invocations via `subprocess.run([sys.executable, "-m", "retrotool", "libsfx", ...])`. Verify exit codes + artifact presence.

---

## Phase I — MBXML <libsfx> element (0.5 day)

**Module:** `retrotool.mbxml.extensions.libsfx`

```xml
<mbxml>
  <libsfx src="./game_src" debug="2" out="@rom"/>
  <rep at="$80FF00">
    <bytes>DE AD BE EF</bytes>
  </rep>
</mbxml>
```

- Runs `LibSFXProject(src).build()` at pipeline position.
- Registers the built ROM under pipeline handle `@rom` (or attribute name).
- Subsequent `<rep>`/`<ins>`/`<asar>` sections operate on the ROM.

**Tests:** integration test: minimal MBXML with `<libsfx>` + single `<rep>` → final ROM has the rep bytes at the rep address AND otherwise matches a vanilla build.

---

## Phase J — Docs + sample + CHANGELOG (0.5 day)

- `README.md` section: "libSFX assembly projects" with 10-line scaffold→build example.
- `examples/libsfx-hello/` — full working scaffolded project with one-sprite demo + step-by-step walkthrough.
- `CHANGELOG.md` 0.9.0 entry listing all new modules.
- Migration note: any 0.8.x user of `retrotool-superfamiconv` → now `retrotool[libsfx]`.

---

## Test strategy

- `tests/asm/`, `tests/audio/`, `tests/rom/`, `tests/compression/`, `tests/debugger/`, `tests/project/`, `tests/mbxml/` — mirror module tree.
- Marker `@pytest.mark.libsfx` — skip if `retrotool-libsfx` not installed. CI installs the local wheel into the test venv as step 0.
- Golden-file comparisons for ROM outputs (store 64-byte header + first/last 256 bytes as fixture).
- `pytest --cov=retrotool` ≥ 80% on new modules before tagging.

---

## Ordering / critical path

```
A (toolchain)          ← must land first
 ├── B (ca65)          ← blocks G
 ├── C (superfamicheck)← blocks G
 ├── D (brr)           ← independent
 ├── E (lz4)           ← independent
 └── F (breakpoints)   ← blocks G (debug=2 path)
       └── G (libsfx)  ← blocks H, I
             ├── H (project.toml + CLI)
             └── I (MBXML)
                   └── J (docs + ship)
```

A-F can parallelize after A. G is the synthesis phase. H/I parallel after G. J last.

**Total calendar estimate:** ~8 working days if serial, ~5 if B-F parallelized.

---

## Open questions

1. **Caching layout**: single `~/.cache/retrotool/` or per-project `.build/`? libSFX projects traditionally use `.build/` — prefer per-project to match convention, fall back to user cache for cross-project shared objects.
2. **Windows parity**: make_breakpoints is bash. Port to Python (~30 lines) so Windows users don't need WSL. Adds ~2 hours.
3. **ca65 parallelism**: `assemble_many` uses `concurrent.futures.ThreadPoolExecutor` or spawn N `ca65` procs? Spawn — ca65 is not thread-safe via shared state, but process-parallel is fine and scales linearly.
4. **Error surface**: do we raise on non-zero exit or always return Result with `returncode`? Raise by default, add `check=False` kwarg for library users who want to inspect stderr.
5. **Asar integration**: libSFX projects sometimes use asar patches post-link. Out of scope for 0.9.0? Yes — patch in 0.9.1 via existing `retrotool.asar`.

---

## Deliverables checklist (release gate)

- [x] Phase A — `retrotool._toolchain` + migrated graphics wrapper
- [x] Phase B — `retrotool.asm.ca65` + tests
- [x] Phase C — `retrotool.rom.header` + tests
- [x] Phase D — `retrotool.audio.brr` + tests
- [x] Phase E — `retrotool.compression.lz4_cli` + tests
- [x] Phase F — `retrotool.debugger.breakpoints` + tests
- [x] Phase G — `retrotool.asm.libsfx` + `LibSFXProject.build()` green on scaffolded Template
- [x] Phase H — `project.toml [build.libsfx]` + `retrotool libsfx {scaffold,build,info,clean}` CLI
- [x] Phase I — MBXML `<libsfx>` element
- [x] Phase J — README section, `examples/libsfx-hello/`, CHANGELOG 0.9.0
- [x] Smoke test: `retrotool libsfx scaffold demo && retrotool libsfx build demo --debug 2` → 128KB SFC, valid checksum $9F26, sym/map/bp emitted (verified locally 2026-04-15). MBXML `<libsfx>` + `<rep>` composition verified (bytes `DE AD BE EF` landed at offset 0x10).
- [ ] Windows CI green (cibuildwheel + native tests)
- [ ] macOS arm64 CI green
- [x] Coverage ≥ 80% on new modules — currently 90% across the 0.9.0 module set (800 stmts, 78 miss; per-module floor 83% in `audio/brr.py`).
- [ ] PyPI trusted publisher configured for `retrotool-libsfx` + `retrotool`
- [ ] Git tag `libsfx-v0.1.0` + `v0.9.0` pushed

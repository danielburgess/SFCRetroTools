"""libSFX project builder — Python re-implementation of libSFX.make.

Discovers `.s` (CPU), `.s700` (SMP), and `.sgs` (GSU) sources under a project
root, assembles them with ca65 (using libSFX's include tree + per-target
defines), links with ld65 against a Map.cfg, fixes the SNES header checksum
via superfamicheck, and optionally emits Mesen breakpoint files.

Mirrors the behavior of `packages/retrotool-libsfx/vendor/libSFX/libSFX.make`
but replaces make-level dependency tracking with `retrotool.core.cache`
content-hash caching via `Ca65Assembler`.
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from retrotool import _toolchain
from retrotool.asm.ca65 import (
    AsmResult,
    Ca65Assembler,
    Ld65Linker,
    LinkResult,
)
from retrotool.core.cache import BuildCache
from retrotool.debugger.breakpoints import make_mesen_breakpoints
from retrotool.rom.header import HeaderFixResult, fix_rom_header

try:  # Python 3.11+
    import tomllib as _toml  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    import tomli as _toml  # type: ignore[no-redef]


_CPU_EXT = ".s"
_SMP_EXT = ".s700"
_GSU_EXT = ".sgs"


@dataclass
class LibSFXConfig:
    """libSFX build knobs. Defaults mirror `libSFX.make`."""
    stack_size: int = 0x100
    zp_pad_size: int = 0x10
    znmi_size: int = 0x10
    rp_size: int = 0x100
    debug: int = 0                             # 0|1|2
    cpu_sources: list[Path] = field(default_factory=list)
    smp_sources: list[Path] = field(default_factory=list)
    gsu_sources: list[Path] = field(default_factory=list)
    extra_includes: list[Path] = field(default_factory=list)
    extra_defines: dict[str, str] = field(default_factory=dict)
    map_config: Path | None = None             # default: root/Map.cfg
    main_config: Path | None = None            # default: root/libSFX.cfg (optional)
    obj_dir: Path | None = None                # default: root/.build
    name: str = "out"                          # ROM/artifact base name
    src_dir: Path | None = None                # default: root; else subdir for globbing


@dataclass
class BuildResult:
    rom: Path
    symfile: Path | None
    mapfile: Path | None
    dbgfile: Path | None
    breakpoints: Path | None
    header: HeaderFixResult
    asm_results: dict[Path, AsmResult]
    link_result: LinkResult
    duration_ms: int


def _hex_dollar(n: int) -> str:
    """Format int as ca65 hex literal ($NN)."""
    return f"${n:X}"


def _glob_sources(root: Path, ext: str) -> list[Path]:
    return sorted(p for p in root.rglob(f"*{ext}") if p.is_file())


class LibSFXProject:
    """A libSFX-layout project rooted at `root` buildable via pure-Python orchestration."""

    def __init__(self, root: Path, cfg: LibSFXConfig | None = None) -> None:
        self.root = Path(root).resolve()
        self.cfg = cfg or LibSFXConfig()
        if self.cfg.obj_dir is None:
            self.cfg.obj_dir = self.root / ".build"
        src_root = self.cfg.src_dir if self.cfg.src_dir else self.root
        if not self.cfg.cpu_sources:
            self.cfg.cpu_sources = _glob_sources(src_root, _CPU_EXT)
        if not self.cfg.smp_sources:
            self.cfg.smp_sources = _glob_sources(src_root, _SMP_EXT)
        if not self.cfg.gsu_sources:
            self.cfg.gsu_sources = _glob_sources(src_root, _GSU_EXT)
        if self.cfg.map_config is None:
            cand = self.root / "Map.cfg"
            # fallback to bare `Map.cfg` — ld65 resolves via --cfg-path
            self.cfg.map_config = cand if cand.exists() else Path("Map.cfg")
        if self.cfg.main_config is None:
            cand = self.root / "libSFX.cfg"
            self.cfg.main_config = cand if cand.exists() else None

    # ---- discovery --------------------------------------------------------

    @classmethod
    def discover(cls, root: Path) -> "LibSFXProject":
        """Build a project from `root`. Reads `[build.libsfx]` from `project.toml` if present."""
        root = Path(root).resolve()
        cfg = LibSFXConfig()
        proj_toml = root / "project.toml"
        if proj_toml.exists():
            data = _toml.loads(proj_toml.read_text())
            section = data.get("build", {}).get("libsfx", {})
            for key in ("stack_size", "zp_pad_size", "znmi_size", "rp_size", "debug", "name"):
                if key in section:
                    setattr(cfg, key, section[key])
            if "map_config" in section:
                cfg.map_config = (root / section["map_config"]).resolve()
            if "main_config" in section:
                cfg.main_config = (root / section["main_config"]).resolve()
            if "extra_includes" in section:
                cfg.extra_includes = [(root / p).resolve() for p in section["extra_includes"]]
            if "extra_defines" in section:
                cfg.extra_defines = dict(section["extra_defines"])
            if "src" in section:
                cfg.src_dir = (root / section["src"]).resolve()
            if "obj_dir" in section:
                cfg.obj_dir = (root / section["obj_dir"]).resolve()
        # name default: root dir name
        if cfg.name == "out":
            cfg.name = root.name
        return cls(root, cfg)

    # ---- properties -------------------------------------------------------

    def sources(self) -> dict[str, list[Path]]:
        return {
            "cpu": list(self.cfg.cpu_sources),
            "smp": list(self.cfg.smp_sources),
            "gsu": list(self.cfg.gsu_sources),
        }

    def _base_defines(self) -> dict[str, str]:
        d: dict[str, str] = {
            "__STACKSIZE__": _hex_dollar(self.cfg.stack_size),
            "__ZPADSIZE__":  _hex_dollar(self.cfg.zp_pad_size),
            "__ZNMISIZE__":  _hex_dollar(self.cfg.znmi_size),
            "__RPADSIZE__":  _hex_dollar(self.cfg.rp_size),
        }
        if self.cfg.debug >= 1:
            d["__DEBUG__"] = str(self.cfg.debug)
        d.update(self.cfg.extra_defines)
        return d

    def _include_dirs(self) -> list[Path]:
        inc = _toolchain.libsfx_include()
        dirs = [self.root, inc, inc / "Configurations"]
        dirs += list(self.cfg.extra_includes)
        return dirs

    def _obj_path(self, src: Path, new_ext: str) -> Path:
        """Mirror source path under obj_dir, swap extension."""
        rel = src.resolve().relative_to(self.root)
        return (self.cfg.obj_dir / rel).with_suffix(new_ext)

    # ---- build phases -----------------------------------------------------

    def assemble(self, *, cache: BuildCache | None = None) -> dict[Path, AsmResult]:
        inc_dirs = self._include_dirs()
        base_defs = self._base_defines()
        results: dict[Path, AsmResult] = {}

        # libSFX runtime sources — CPU + SMP — always built alongside project sources.
        libsfx_inc = _toolchain.libsfx_include()
        sfx_cpu = sorted((libsfx_inc / "CPU").glob("*.s")) if (libsfx_inc / "CPU").exists() else []
        sfx_smp = sorted((libsfx_inc / "SMP").glob("*.s700")) if (libsfx_inc / "SMP").exists() else []

        def _run(srcs, defs, obj_ext):
            asm = Ca65Assembler(
                include_dirs=inc_dirs, defines=defs,
                debug=self.cfg.debug > 0,
                extra_args=["-U"],   # mark unresolved externals as imports (libSFX convention)
                cache=cache,
            )
            for src in srcs:
                src_r = src.resolve()
                if src_r.is_relative_to(self.root):
                    obj = self._obj_path(src, obj_ext)
                else:
                    if src_r.is_relative_to(libsfx_inc):
                        rel = src_r.relative_to(libsfx_inc)
                    else:
                        rel = Path(src_r.name)
                    flat = rel.with_suffix(obj_ext).as_posix().replace("/", "_")
                    obj = self.cfg.obj_dir / "_libsfx" / flat
                results[src] = asm.assemble(src, out_obj=obj)

        _run(self.cfg.cpu_sources + sfx_cpu, base_defs, ".o")
        smp_defs = {**base_defs, "TARGET_SMP": "1"}
        _run(self.cfg.smp_sources + sfx_smp, smp_defs, ".o700")
        gsu_defs = {**base_defs, "TARGET_GSU": "1"}
        _run(self.cfg.gsu_sources, gsu_defs, ".ogs")
        return results

    def link(self, objs: list[Path], out_rom: Path) -> LinkResult:
        cfg_dirs = [self.root, _toolchain.libsfx_config()]
        linker = Ld65Linker(
            config=self.cfg.map_config,
            cfg_dirs=cfg_dirs,
            debug_level=self.cfg.debug,
        )
        return linker.link(objs, out_rom)

    def post_process(self, rom: Path) -> HeaderFixResult:
        return fix_rom_header(rom)

    def build(self, out_rom: Path | None = None, *,
              cache: BuildCache | None = None) -> BuildResult:
        t0 = time.monotonic()
        out_rom = Path(out_rom) if out_rom else (self.root / f"{self.cfg.name}.sfc")

        asm_results = self.assemble(cache=cache)
        objs = [r.obj for r in asm_results.values()]
        link_result = self.link(objs, out_rom)
        header = self.post_process(out_rom)

        bp_path: Path | None = None
        if self.cfg.debug >= 1 and link_result.symfile is not None:
            try:
                bp_path = make_mesen_breakpoints(link_result.symfile)
            except Exception:
                bp_path = None

        return BuildResult(
            rom=out_rom,
            symfile=link_result.symfile,
            mapfile=link_result.mapfile,
            dbgfile=link_result.dbgfile,
            breakpoints=bp_path,
            header=header,
            asm_results=asm_results,
            link_result=link_result,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    def clean(self) -> None:
        if self.cfg.obj_dir and self.cfg.obj_dir.exists():
            shutil.rmtree(self.cfg.obj_dir)


def scaffold_libsfx_project(dest: Path, template: str = "Template") -> Path:
    """Copy libSFX `examples/<template>/` to `dest` and write a minimal `project.toml`.

    Returns the scaffolded project root.
    """
    dest = Path(dest).resolve()
    examples = _toolchain.libsfx_examples()
    src = examples / template
    if not src.exists():
        available = sorted(p.name for p in examples.iterdir() if p.is_dir())
        raise FileNotFoundError(
            f"template {template!r} not found in {examples}. Available: {available}"
        )
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} exists and is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        if entry.is_dir():
            shutil.copytree(entry, dest / entry.name)
        else:
            shutil.copy2(entry, dest / entry.name)

    proj_toml = dest / "project.toml"
    if not proj_toml.exists():
        proj_toml.write_text(
            "[build.libsfx]\n"
            f'name = "{dest.name}"\n'
            "debug = 0\n"
        )
    return dest

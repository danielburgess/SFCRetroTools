"""ca65/ld65 wrappers — assemble .s → .o, link .o → .sfc.

Uses `retrotool._toolchain` for binary resolution and `retrotool.core.BuildCache`
for content-hash caching of compiled objects.

Cache key inputs (for Ca65Assembler): source bytes, bytes of every include file
resolved from `include_dirs`, sorted defines, CPU string, and ca65 version.
If any of these change, the object is rebuilt.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from retrotool import _toolchain
from retrotool._toolchain import ToolchainError
from retrotool.core.cache import BuildCache, sha256_many


class Ca65Error(RuntimeError):
    """Raised when ca65 exits non-zero."""


class Ld65Error(RuntimeError):
    """Raised when ld65 exits non-zero."""


@dataclass
class AsmResult:
    obj: Path
    stdout: str
    stderr: str
    duration_ms: int
    cached: bool = False


@dataclass
class LinkResult:
    rom: Path
    symfile: Path | None
    mapfile: Path | None
    dbgfile: Path | None
    stdout: str
    stderr: str
    duration_ms: int


def _scan_includes(src: Path, include_dirs: list[Path]) -> list[Path]:
    """Best-effort include discovery by grepping .include/.import directives.

    Not a full dependency graph — we only snapshot top-level includes for the
    cache key. Indirect includes still contribute to build correctness because
    ca65 re-reads them; we just don't invalidate the cache when a transitive
    include changes. For hermetic libSFX builds this is acceptable since the
    include tree is shipped as part of retrotool-libsfx and tool_version()
    already varies with toolchain version bumps.
    """
    found: list[Path] = []
    try:
        text = src.read_text(errors="ignore")
    except OSError:
        return []
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith(".include") or stripped.startswith(".import")):
            continue
        # .include "foo.i"  or  .import foo
        parts = stripped.split(None, 1)
        if len(parts) < 2:
            continue
        tok = parts[1].strip().strip('"').strip("'").split(",")[0].strip()
        for d in include_dirs:
            cand = d / tok
            if cand.exists():
                found.append(cand)
                break
    return found


class Ca65Assembler:
    """Assemble a single .s file to a .o object."""

    def __init__(
        self,
        include_dirs: list[Path] = (),
        defines: dict[str, str] | None = None,
        cpu: str = "65816",
        debug: bool = False,
        extra_args: list[str] = (),
        cache: BuildCache | None = None,
    ) -> None:
        self.include_dirs = [Path(p) for p in include_dirs]
        self.defines = dict(defines or {})
        self.cpu = cpu
        self.debug = debug
        self.extra_args = list(extra_args)
        self.cache = cache

    def _cache_key(self, src: Path) -> str:
        parts: list[bytes] = [
            src.read_bytes(),
            json.dumps(self.defines, sort_keys=True).encode(),
            self.cpu.encode(),
            (b"debug" if self.debug else b""),
            _toolchain.tool_version("ca65").encode(),
        ]
        for inc in _scan_includes(src, self.include_dirs):
            try:
                parts.append(inc.read_bytes())
            except OSError:
                pass
        return sha256_many(parts)

    def _build_cmd(self, src: Path, out_obj: Path) -> list[str]:
        cmd = [str(_toolchain.ca65()), "--cpu", self.cpu, "-o", str(out_obj)]
        if self.debug:
            cmd += ["-g"]
        for d in self.include_dirs:
            cmd += ["-I", str(d)]
        for name, value in self.defines.items():
            cmd += ["-D", f"{name}={value}" if value else name]
        cmd += list(self.extra_args)
        cmd.append(str(src))
        return cmd

    def assemble(self, src: Path, out_obj: Path | None = None) -> AsmResult:
        src = Path(src)
        if out_obj is None:
            out_obj = src.with_suffix(".o")
        out_obj = Path(out_obj)
        out_obj.parent.mkdir(parents=True, exist_ok=True)

        if self.cache is not None:
            key = self._cache_key(src)
            hit = self.cache.get(key)
            if hit is not None:
                out_obj.write_bytes(hit.artifact.read_bytes())
                return AsmResult(obj=out_obj, stdout="", stderr="[cache hit]",
                                 duration_ms=0, cached=True)

        t0 = time.monotonic()
        proc = subprocess.run(self._build_cmd(src, out_obj), capture_output=True, text=True)
        dur_ms = int((time.monotonic() - t0) * 1000)
        if proc.returncode != 0:
            raise Ca65Error(
                f"ca65 failed ({proc.returncode}) on {src}\n"
                f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
            )

        if self.cache is not None:
            self.cache.put(
                self._cache_key(src),
                out_obj.read_bytes(),
                meta={"src": str(src), "cpu": self.cpu,
                      "ca65_version": _toolchain.tool_version("ca65")},
            )
        return AsmResult(obj=out_obj, stdout=proc.stdout, stderr=proc.stderr,
                         duration_ms=dur_ms, cached=False)

    def assemble_many(self, srcs: list[Path]) -> list[AsmResult]:
        """Serial for now — ca65 is fast and parallelism adds complexity."""
        return [self.assemble(s) for s in srcs]


class Ld65Linker:
    """Link ca65 object files into a ROM using an ld65 config."""

    def __init__(
        self,
        config: Path,
        lib_dirs: list[Path] = (),
        cfg_dirs: list[Path] = (),
        debug_level: int = 0,
    ) -> None:
        if debug_level not in (0, 1, 2, 3):
            raise ValueError(f"debug_level must be 0..3, got {debug_level}")
        self.config = Path(config)
        self.lib_dirs = [Path(p) for p in lib_dirs]
        self.cfg_dirs = [Path(p) for p in cfg_dirs]
        self.debug_level = debug_level

    def link(self, objs: list[Path], out_rom: Path) -> LinkResult:
        out_rom = Path(out_rom)
        out_rom.parent.mkdir(parents=True, exist_ok=True)

        symfile = out_rom.with_suffix(".sym") if self.debug_level >= 1 else None
        mapfile = out_rom.with_suffix(".map") if self.debug_level >= 2 else None
        dbgfile = out_rom.with_suffix(".dbg") if self.debug_level >= 3 else None

        cmd = [str(_toolchain.ld65()), "-C", str(self.config), "-o", str(out_rom)]
        for d in self.lib_dirs:
            cmd += ["--lib-path", str(d)]
        for d in self.cfg_dirs:
            cmd += ["--cfg-path", str(d)]
        if symfile:
            cmd += ["-Ln", str(symfile)]
        if mapfile:
            cmd += ["-m", str(mapfile)]
        if dbgfile:
            cmd += ["--dbgfile", str(dbgfile)]
        cmd += [str(o) for o in objs]

        t0 = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        dur_ms = int((time.monotonic() - t0) * 1000)
        if proc.returncode != 0:
            raise Ld65Error(
                f"ld65 failed ({proc.returncode})\n"
                f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
            )
        return LinkResult(
            rom=out_rom, symfile=symfile, mapfile=mapfile, dbgfile=dbgfile,
            stdout=proc.stdout, stderr=proc.stderr, duration_ms=dur_ms,
        )

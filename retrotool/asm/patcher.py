"""Asar patch orchestration. Wraps `asar` CLI or Python bindings."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from retrotool.core.cache import BuildCache, sha256_file, sha256_many


@dataclass
class PatchResult:
    ok: bool
    output_rom: Path
    log: str = ""
    cache_hit: bool = False


@dataclass
class AsarPatch:
    asm_file: Path
    includes: list[Path] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)


def _key(rom: Path, patch: AsarPatch) -> str:
    parts: list[bytes] = [sha256_file(rom).encode(), sha256_file(patch.asm_file).encode()]
    for inc in patch.includes:
        if inc.exists():
            parts.append(sha256_file(inc).encode())
    for k, v in sorted(patch.defines.items()):
        parts.append(f"{k}={v}".encode())
    return sha256_many(parts)


def _asar_bundled() -> Optional[str]:
    """Resolve the bundled `retrotool-asar` binary, if that wheel is installed."""
    try:
        from retrotool_asar import asar_binary, ToolNotBundledError
    except ImportError:
        return None
    try:
        return str(asar_binary())
    except ToolNotBundledError:
        return None


def _resolve_asar(asar_cmd: str) -> Optional[str]:
    """Prefer caller-given path, then bundled wheel, then system asar on PATH."""
    if asar_cmd != "asar":
        return shutil.which(asar_cmd) or asar_cmd  # explicit override; trust caller
    return _asar_bundled() or shutil.which("asar")


def apply_patch(
    rom: Path,
    patch: AsarPatch,
    out: Path,
    cache: Optional[BuildCache] = None,
    asar_cmd: str = "asar",
) -> PatchResult:
    """Apply an Asar patch to `rom` → `out`. Uses BuildCache when provided.

    asar resolution order: explicit `asar_cmd` override (if not the default) →
    bundled `retrotool-asar` wheel → system `asar` on PATH.
    """
    key = _key(rom, patch) if cache else None
    if cache and key and cache.has(key):
        entry = cache.get(key)
        out.write_bytes(entry.artifact.read_bytes())
        return PatchResult(ok=True, output_rom=out, cache_hit=True)

    binary = _resolve_asar(asar_cmd)
    if binary is None:
        return PatchResult(
            ok=False, output_rom=out,
            log=(
                f"asar binary not found — install `retrotool-asar` (bundled) "
                f"or put `asar` on PATH"
            ),
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(rom.read_bytes())

    defines = []
    for k, v in patch.defines.items():
        defines += ["-D", f"{k}={v}"]
    cmd = [binary, *defines, str(patch.asm_file), str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    log = (proc.stdout or "") + (proc.stderr or "")

    if proc.returncode != 0:
        return PatchResult(ok=False, output_rom=out, log=log)

    if cache and key:
        cache.put(key, out.read_bytes(), meta={"rom": str(rom), "patch": str(patch.asm_file)})

    return PatchResult(ok=True, output_rom=out, log=log)

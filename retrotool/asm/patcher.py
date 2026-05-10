"""Assembler patch orchestration.

Two assemblers are supported, each wrapped behind the same `PatchResult`
shape so callers (notably `retrotool.build.handlers`) can swap them with
nothing more than a different `kind=` on the section:

  * **asar** — `AsarPatch` + `apply_patch()`. Default for retrotool projects.
  * **bass v18 (ARM9 fork)** — `BassPatch` + `apply_bass_patch()`. Mirror of
    asar; uses `bass -m <rom>` modify-mode so the assembler patches the
    target ROM in place exactly the way asar does.

Both helpers prefer a bundled-wheel binary (`retrotool_asar` /
`retrotool_bass`) when present, fall back to a system binary on PATH, and
raise the same `PatchResult(ok=False, log=…)` for "binary not found".
"""
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
        # Asar's CLI tokenizer splits on whitespace inside the define value, so
        # `PATCH_TITLE=My Game` becomes two tokens. Reject the ambiguous chars
        # rather than silently corrupting the patch.
        sv = str(v)
        if any(ch in sv for ch in ' \t\n"='):
            raise ValueError(
                f"asar define value for {k!r} contains whitespace, '\"', or '=' "
                f"({sv!r}) — asar's CLI tokenizer cannot represent it. "
                f"Use a `!define` line in an .asm include instead."
            )
        defines += ["-D", f"{k}={sv}"]
    cmd = [binary, *defines, str(patch.asm_file), str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    log = (proc.stdout or "") + (proc.stderr or "")

    if proc.returncode != 0:
        return PatchResult(ok=False, output_rom=out, log=log)

    if cache and key:
        cache.put(key, out.read_bytes(), meta={"rom": str(rom), "patch": str(patch.asm_file)})

    return PatchResult(ok=True, output_rom=out, log=log)


# ---- bass v18 (ARM9 fork) -------------------------------------------------

@dataclass
class BassPatch:
    """Mirror of `AsarPatch` for bass v18 (ARM9 fork).

    `asm_file` is the entry source. `includes` are extra include-search
    directories; bass already resolves `include "rel/path.asm"` against
    the entry's own directory, so this is for sibling search roots only.

    `defines` map to `-d KEY=VALUE` (string substitution at parse time).
    `constants` map to `-c KEY=VALUE` (numeric symbols visible to the
    assembler). bass treats the two distinctly; asar collapses them onto
    `-D`. Default is empty for both.

    `strict` toggles `-strict` (warnings become errors). Off by default
    to match asar's loose default behavior.
    """
    asm_file: Path
    includes: list[Path] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    constants: dict[str, str] = field(default_factory=dict)
    strict: bool = False


def _key_bass(rom: Path, patch: BassPatch) -> str:
    parts: list[bytes] = [
        sha256_file(rom).encode(), sha256_file(patch.asm_file).encode(),
    ]
    for inc in patch.includes:
        if inc.exists():
            parts.append(sha256_file(inc).encode())
    for k, v in sorted(patch.defines.items()):
        parts.append(f"d:{k}={v}".encode())
    for k, v in sorted(patch.constants.items()):
        parts.append(f"c:{k}={v}".encode())
    if patch.strict:
        parts.append(b"strict")
    return sha256_many(parts)


def _bass_bundled() -> Optional[str]:
    """Resolve the bundled `retrotool-bass` binary, if that wheel exists.

    Mirrors `_asar_bundled()`. The wheel is optional; this returns None
    cleanly when it isn't installed so the resolver can fall back to a
    system `bass` on PATH.
    """
    try:
        from retrotool_bass import bass_binary, ToolNotBundledError  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        return str(bass_binary())
    except ToolNotBundledError:
        return None


def _resolve_bass(bass_cmd: str) -> Optional[str]:
    """Prefer caller-given path, then bundled wheel, then system bass on PATH."""
    if bass_cmd != "bass":
        return shutil.which(bass_cmd) or bass_cmd  # explicit override; trust caller
    return _bass_bundled() or shutil.which("bass")


def _validate_bass_kv(label: str, k: str, v: str) -> None:
    """bass tokenizes `-d`/`-c` arguments in `arguments.take(...)` and then
    splits the value on the first `=`. Whitespace inside the value would be
    eaten by the shell; embedded `"` survives the shell but would corrupt
    the assembler's parser. Reject the ambiguous chars rather than silently
    corrupting the patch — same guard asar's wrapper applies to defines.
    """
    if any(ch in v for ch in ' \t\n"'):
        raise ValueError(
            f"bass {label} value for {k!r} contains whitespace or '\"' "
            f"({v!r}) — bass's CLI tokenizer cannot represent it. "
            f"Use a `define` line in an include instead."
        )


def apply_bass_patch(
    rom: Path,
    patch: BassPatch,
    out: Path,
    cache: Optional[BuildCache] = None,
    bass_cmd: str = "bass",
) -> PatchResult:
    """Apply a bass v18 patch to `rom` → `out`. Mirror of `apply_patch`.

    Resolution order: explicit `bass_cmd` override (if not the default) →
    bundled `retrotool_bass` wheel → system `bass` on PATH.

    Implementation: copy `rom` to `out`, then run
    ``bass -m <out> [-strict] [-d K=V ...] [-c K=V ...] <patch.asm_file>``.
    The `-m` (modify) mode patches the target file in place, which is the
    asar-equivalent semantic; `-o` (overwrite) would discard the source ROM
    bytes entirely and is wrong here.
    """
    # Validate caller-supplied -d / -c values *before* checking the binary.
    # Catches malformed input even when bass isn't installed locally — same
    # ordering as input validation in any well-behaved CLI wrapper.
    for k, v in patch.defines.items():
        _validate_bass_kv("define", k, str(v))
    for k, v in patch.constants.items():
        _validate_bass_kv("constant", k, str(v))

    key = _key_bass(rom, patch) if cache else None
    if cache and key and cache.has(key):
        entry = cache.get(key)
        out.write_bytes(entry.artifact.read_bytes())
        return PatchResult(ok=True, output_rom=out, cache_hit=True)

    binary = _resolve_bass(bass_cmd)
    if binary is None:
        return PatchResult(
            ok=False, output_rom=out,
            log=(
                f"bass binary not found — install `retrotool-bass` (when "
                f"available) or put `bass` on PATH "
                f"(github.com/ARM9/bass)"
            ),
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(rom.read_bytes())

    cli: list[str] = [binary, "-m", str(out)]
    if patch.strict:
        cli.append("-strict")
    for k, v in patch.defines.items():
        cli += ["-d", f"{k}={v}"]
    for k, v in patch.constants.items():
        cli += ["-c", f"{k}={v}"]
    # bass searches for includes against the entry file's directory by
    # default; pass extra include dirs by symlinking / cwd-ing into them is
    # not portable. Honor user-supplied include dirs by chdir'ing into the
    # patch's parent and passing absolute paths — bass's literal `include`
    # directive then resolves relative paths against the entry's own dir,
    # which is the expected semantic. Additional dirs in `patch.includes`
    # only contribute to the cache key (so edits in those dirs invalidate
    # the cached artifact); bass itself doesn't have a `-I` equivalent.
    cli.append(str(patch.asm_file))

    proc = subprocess.run(cli, capture_output=True, text=True)
    log = (proc.stdout or "") + (proc.stderr or "")

    if proc.returncode != 0:
        return PatchResult(ok=False, output_rom=out, log=log)

    if cache and key:
        cache.put(
            key, out.read_bytes(),
            meta={"rom": str(rom), "patch": str(patch.asm_file), "asm": "bass"},
        )

    return PatchResult(ok=True, output_rom=out, log=log)

"""Transitive incsrc / incbin / include dependency scan for asar patches.

Walks an asar source file, collects referenced include files (incsrc,
incbin, and the bare `include` directive), resolves each against the
patch's own directory plus any user-supplied include dirs, and recurses
through incsrc/include targets. Used by the BuildCache key so editing an
included .asm / .bin invalidates cached asar output.

Scope / intentional limits:

  * Parses literal string paths only. Dynamic paths built from `!define`
    expansions, math, or macro concatenation are NOT followed — those
    would require a full asar parser. For the LM3-style patches this
    tool targets (pointer relocation, fixed writes, small VWF helpers)
    all includes are static literals.
  * Line-oriented scan: strips `//` and `;` line comments and `/* */`
    block comments before matching.
  * Skips any target that fails to resolve (missing includes are the
    assembler's problem, not ours; the cache just hashes what exists).
  * Returns files in a deterministic order (sorted by absolute path)
    so the cache key is stable across runs.

The scanner is conservative: if in doubt it includes a candidate file,
since over-hashing costs nothing but under-hashing yields stale cache.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


# incsrc  "path/to/file.asm"       — recursed into
# incsrc  path/to/file.asm         — unquoted form (asar accepts)
# include "path/to/file.asm"       — bare `include` (asar treats as incsrc)
# incbin  "path/to/file.bin"       — hashed only, not recursed
# incbin  "path/to/file.bin":0-10  — offset/range suffix ignored for pathing
_INCSRC_RE = re.compile(
    r"""^\s*(?:incsrc|include)\s+(?:"([^"]+)"|'([^']+)'|(\S+))""",
    re.IGNORECASE,
)
_INCBIN_RE = re.compile(
    r"""^\s*incbin\s+(?:"([^"]+)"|'([^']+)'|([^\s:]+))""",
    re.IGNORECASE,
)

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(text: str) -> str:
    text = _BLOCK_COMMENT_RE.sub("", text)
    out_lines: list[str] = []
    for line in text.splitlines():
        # Strip // and ; line comments. Naive: does not respect strings.
        # OK for directive-line scanning — paths with `;` or `//` are not
        # supported by asar anyway.
        for marker in ("//", ";"):
            idx = line.find(marker)
            if idx >= 0:
                line = line[:idx]
        out_lines.append(line)
    return "\n".join(out_lines)


def _resolve_candidate(ref: str, anchor_dir: Path, include_dirs: Iterable[Path]) -> Path | None:
    """Find `ref` relative to anchor_dir first, then each include dir."""
    p = Path(ref)
    if p.is_absolute():
        return p if p.exists() else None
    # Anchor = directory of the file that declared the include.
    cand = (anchor_dir / p).resolve()
    if cand.exists():
        return cand
    for inc in include_dirs:
        cand = (Path(inc) / p).resolve()
        if cand.exists():
            return cand
    return None


def scan_deps(
    entry: Path,
    *,
    include_dirs: Iterable[Path] = (),
    _seen: set[Path] | None = None,
) -> list[Path]:
    """Return entry + every transitively reachable incsrc/incbin file.

    The entry itself is first in the returned list; dependencies follow
    in sorted (absolute-path) order. Cycles are guarded via `_seen`.
    Unresolvable references are silently skipped — they'll surface as
    real asar errors when the patch runs."""
    entry = entry.resolve()
    if _seen is None:
        _seen = set()
    if entry in _seen or not entry.exists():
        return []
    _seen.add(entry)

    results: list[Path] = [entry]
    try:
        text = entry.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return results
    stripped = _strip_comments(text)

    children_src: list[Path] = []
    children_bin: list[Path] = []

    for line in stripped.splitlines():
        m = _INCSRC_RE.match(line)
        if m:
            ref = next(g for g in m.groups() if g)
            cand = _resolve_candidate(ref, entry.parent, include_dirs)
            if cand is not None:
                children_src.append(cand)
            continue
        m = _INCBIN_RE.match(line)
        if m:
            ref = next(g for g in m.groups() if g)
            cand = _resolve_candidate(ref, entry.parent, include_dirs)
            if cand is not None:
                children_bin.append(cand)
            continue

    # Recurse into .asm sources; .bin files get hashed but not walked.
    for child in children_src:
        results.extend(scan_deps(child, include_dirs=include_dirs, _seen=_seen))
    for child in children_bin:
        if child not in _seen:
            _seen.add(child)
            results.append(child)

    # Keep entry first, rest sorted for key stability.
    head, tail = results[0], results[1:]
    tail.sort()
    return [head, *tail]

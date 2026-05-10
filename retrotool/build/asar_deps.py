"""Transitive include / incbin dependency scan for assembler patches.

Walks an entry source file, collects referenced include files, resolves
each against the patch's own directory plus any user-supplied include
dirs, and recurses through source-include targets. Used by the BuildCache
key so editing an included `.asm` / `.bin` invalidates cached output.

Two dialects share the same scaffolding:

  * **asar** (`scan_deps`) — `incsrc "file"`, bare `include "file"`,
    `incbin "file"[:offset-len]`. Default; backwards compatible.
  * **bass v18** (`scan_bass_deps`) — `include "file"` (source) and
    `insert [name, ] "file"[, offset[, length]]` (binary).

Scope / intentional limits (apply to both dialects):

  * Parses literal string paths only. Dynamic paths built from `!define`
    expansions, math, or macro concatenation are NOT followed — those
    would require a full assembler parser. For the kind of patches this
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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# ---- asar regexes ---------------------------------------------------------
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

# ---- bass regexes ---------------------------------------------------------
# include "path/to/file.asm"                                — recursed into
# insert  "path/to/file.bin"                                — hashed only
# insert  name, "path/to/file.bin"                          — hashed only
# insert  "path/to/file.bin", $offset, $length              — hashed only
# Bass accepts only quoted paths in its grammar; we still tolerate unquoted
# tokens conservatively because the cache cost of a false-positive hash
# is negligible. The leading `name,` form for `insert` is matched by
# allowing an optional `<ident>,\s*` prefix before the path.
_BASS_INCLUDE_RE = re.compile(
    r"""^\s*include\s+(?:"([^"]+)"|'([^']+)')""",
    re.IGNORECASE,
)
_BASS_INSERT_RE = re.compile(
    r"""^\s*insert\s+(?:[A-Za-z_][A-Za-z0-9_]*\s*,\s*)?"""
    r"""(?:"([^"]+)"|'([^']+)')""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Dialect:
    """Per-dialect regex bundle. Source matches recurse; binary matches
    are only hashed."""
    src_re: re.Pattern
    bin_re: re.Pattern


_ASAR = _Dialect(src_re=_INCSRC_RE, bin_re=_INCBIN_RE)
_BASS = _Dialect(src_re=_BASS_INCLUDE_RE, bin_re=_BASS_INSERT_RE)

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


def _scan(
    entry: Path,
    dialect: _Dialect,
    *,
    include_dirs: Iterable[Path] = (),
    _seen: set[Path] | None = None,
) -> list[Path]:
    """Dialect-parameterized recursive scan. Public callers go through
    `scan_deps` (asar) or `scan_bass_deps` (bass)."""
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
        m = dialect.src_re.match(line)
        if m:
            ref = next(g for g in m.groups() if g)
            cand = _resolve_candidate(ref, entry.parent, include_dirs)
            if cand is not None:
                children_src.append(cand)
            continue
        m = dialect.bin_re.match(line)
        if m:
            ref = next(g for g in m.groups() if g)
            cand = _resolve_candidate(ref, entry.parent, include_dirs)
            if cand is not None:
                children_bin.append(cand)
            continue

    # Recurse into source includes; binary inserts are hashed but not walked.
    for child in children_src:
        results.extend(_scan(child, dialect, include_dirs=include_dirs, _seen=_seen))
    for child in children_bin:
        if child not in _seen:
            _seen.add(child)
            results.append(child)

    head, tail = results[0], results[1:]
    tail.sort()
    return [head, *tail]


def scan_deps(
    entry: Path,
    *,
    include_dirs: Iterable[Path] = (),
    _seen: set[Path] | None = None,
) -> list[Path]:
    """Return entry + every transitively reachable incsrc/incbin file (asar
    dialect).

    The entry itself is first in the returned list; dependencies follow
    in sorted (absolute-path) order. Cycles are guarded via `_seen`.
    Unresolvable references are silently skipped — they'll surface as
    real asar errors when the patch runs."""
    return _scan(entry, _ASAR, include_dirs=include_dirs, _seen=_seen)


def scan_bass_deps(
    entry: Path,
    *,
    include_dirs: Iterable[Path] = (),
    _seen: set[Path] | None = None,
) -> list[Path]:
    """Return entry + every transitively reachable include/insert file
    (bass v18 ARM9-fork dialect).

    bass differs from asar at the *directive* level: source includes are
    spelled `include "file.asm"` (no `incsrc`), and binary inserts are
    `insert [name, ] "file.bin" [, offset [, length]]`. Otherwise the
    walk semantics, comment stripping, and ordering guarantees match
    `scan_deps`."""
    return _scan(entry, _BASS, include_dirs=include_dirs, _seen=_seen)

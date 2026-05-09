"""Per-block / per-window targeting filter for `<script>` sections.

Extends `--only` so a single section can be narrowed to one block (or a
range), and a block can be narrowed to one window (or a range). Used for
debugging large scripts where a re-encoded entry corrupts something
downstream — restricting the build to a single block isolates the cause
without rebuilding the rest of the script.

Built and consumed exclusively by the build pipeline; CLI parses raw
`--only` tokens with `parse_only_token()` and assembles the rules.

Wiring:
    cli.py         — `_parse_only_specs()` produces (only_set, ScriptFilter)
    driver.build() — accepts `script_filter=` kwarg, passes through ctx
    handlers.py    — `_script_prepare_*` / `_emit_*` mask non-allowed entries
                     to b"\\x00" (overflow's preserve-source sentinel) and
                     drop non-allowed windows from the windowed payload.

Block/window filters require `placement.mode = "overflow"` because relocate
mode rewrites the whole pointer table — selectively rebuilding one entry
without rewriting its neighbors would risk pointer drift. Section-level
filtering (no block/window suffix) works in any placement mode.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class IndexRange:
    """Inclusive integer range, optionally an open lower or upper bound.

    `lo`/`hi` mirror the user-facing `BLOCK[-BLOCKEND]` syntax: a single int
    becomes `IndexRange(n, n)`; `-N` becomes `(0, N)`; `N-` becomes `(N, ∞)`.
    """
    lo: int
    hi: int  # inclusive

    def contains(self, idx: int) -> bool:
        return self.lo <= idx <= self.hi

    @classmethod
    def parse(cls, text: str) -> "IndexRange":
        """Parse `"N"`, `"N-M"`, `"-M"` (means `0-M`), or `"N-"` (means
        `N-2**31`). Raises ValueError on malformed input."""
        s = text.strip()
        if not s:
            raise ValueError("empty range")
        if "-" not in s:
            n = int(s, 0)
            if n < 0:
                raise ValueError(f"negative index: {s!r}")
            return cls(n, n)
        lo_s, _, hi_s = s.partition("-")
        if not lo_s and not hi_s:
            raise ValueError(f"empty range bounds: {s!r}")
        lo = int(lo_s, 0) if lo_s else 0
        hi = int(hi_s, 0) if hi_s else (1 << 31) - 1
        if lo < 0 or hi < 0:
            raise ValueError(f"negative bound in range: {s!r}")
        if lo > hi:
            raise ValueError(f"range lo > hi: {s!r}")
        return cls(lo, hi)


@dataclass(frozen=True)
class ScriptTarget:
    """One filter rule. `block_range=None` means "all blocks" (section-level
    filter only). `window_range=None` means "all windows in matched blocks".
    """
    section_id: str  # lowercased; matched against the `--only` ID set
    block_range: Optional[IndexRange] = None
    window_range: Optional[IndexRange] = None


@dataclass
class ScriptFilter:
    """Bag of `ScriptTarget`s, indexed by section_id for cheap lookup.

    A section appearing in `targets_by_id` with at least one rule that has a
    block_range is considered "narrowed" — entries outside any rule's
    block_range are filtered out. A section with only block_range=None
    rules is unaffected by block filtering (section-level only).
    """
    targets_by_id: dict[str, list[ScriptTarget]] = field(default_factory=dict)

    def add(self, target: ScriptTarget) -> None:
        self.targets_by_id.setdefault(target.section_id.lower(), []).append(target)

    def is_empty(self) -> bool:
        return not self.targets_by_id

    def matching_targets(self, section_ids: set[str]) -> list[ScriptTarget]:
        """Return all targets whose section_id appears in `section_ids`
        (already lowercased — caller usually passes the same set the
        section-level filter computes)."""
        out: list[ScriptTarget] = []
        ids = {s.lower() for s in section_ids}
        for sid, rules in self.targets_by_id.items():
            if sid in ids:
                out.extend(rules)
        return out

    def has_block_filter(self, section_ids: set[str]) -> bool:
        """True if any matching target restricts blocks. Used by handlers to
        decide whether to consult `block_allowed()`."""
        return any(t.block_range is not None
                   for t in self.matching_targets(section_ids))

    def has_window_filter(self, section_ids: set[str]) -> bool:
        return any(t.window_range is not None
                   for t in self.matching_targets(section_ids))

    def block_allowed(self, section_ids: set[str], block_idx: int) -> bool:
        """`section_ids` are the IDs the section presents to the filter — the
        same set `_section_kinds_filter` computes (kind, name, datadef name,
        etc.). When no rule restricts blocks for this section, all blocks pass.
        """
        rules = self.matching_targets(section_ids)
        block_rules = [t for t in rules if t.block_range is not None]
        if not block_rules:
            return True
        return any(t.block_range.contains(block_idx) for t in block_rules)

    def window_allowed(
        self, section_ids: set[str], block_idx: int, window_idx: int,
    ) -> bool:
        """`window_allowed` only considers rules that *also* match the block.
        A rule with `window_range=N` and `block_range=42` only narrows
        windows when the block is 42; other blocks see "no window filter".
        """
        rules = self.matching_targets(section_ids)
        # Rules that match this block AND have a window_range
        active = [
            t for t in rules
            if t.window_range is not None
            and (t.block_range is None or t.block_range.contains(block_idx))
        ]
        if not active:
            return True
        return any(t.window_range.contains(window_idx) for t in active)


def parse_only_token(token: str) -> tuple[str, Optional[IndexRange], Optional[IndexRange]]:
    """Parse one `--only` token into `(section_id, block_range, window_range)`.

    Grammar:
        token       := SECTION (':' BLOCK_SPEC (':' WINDOW_SPEC)?)?
        BLOCK_SPEC  := INT | INT '-' INT | '-' INT | INT '-'
        WINDOW_SPEC := same as BLOCK_SPEC

    Examples (passing through the section-level matcher unchanged):
        "asar"               -> ("asar", None, None)
        "dialog-1"           -> ("dialog-1", None, None)
        "dialog-1:42"        -> ("dialog-1", [42, 42], None)
        "dialog-1:42-50"     -> ("dialog-1", [42, 50], None)
        "dialog-1:42:0"      -> ("dialog-1", [42, 42], [0, 0])
        "dialog-1:42:0-3"    -> ("dialog-1", [42, 42], [0, 3])
        "dialog-1:42-50:0-3" -> ("dialog-1", [42, 50], [0, 3])

    Section IDs that legitimately contain `:` (e.g. `project.toml:sections[7]`)
    are passed through as-is when no trailing block spec is present — we only
    treat `:` as a block-spec separator when the part after it parses as a
    range.
    """
    raw = token.strip()
    if not raw:
        raise ValueError("empty filter token")
    if ":" not in raw:
        return raw, None, None

    # Try parsing the rightmost colon-separated suffix as a range. If it
    # doesn't parse as a range, the colon belongs to the section ID
    # (e.g. "project.toml:sections[7]") and the whole token is a section.
    head, _, last = raw.rpartition(":")
    if not _looks_like_range(last):
        return raw, None, None

    # Now decide whether `head` ends in another range (block) or is the
    # whole section ID (so `last` is the block spec, no window).
    if ":" in head:
        section, _, mid = head.rpartition(":")
        if _looks_like_range(mid):
            block = IndexRange.parse(mid)
            window = IndexRange.parse(last)
            return section, block, window
    # Single suffix → block only.
    return head, IndexRange.parse(last), None


def _looks_like_range(s: str) -> bool:
    """Cheap heuristic: anchored digits and at most one `-` separating two
    optional digit groups. Avoids accepting things like `sections[7]`."""
    s = s.strip()
    if not s:
        return False
    parts = s.split("-")
    if len(parts) > 2:
        return False
    for p in parts:
        if p and not p.lstrip("0").isdigit() and p != "0":
            # Allow "0" specifically (lstrip("0") empties it).
            if not (p.startswith(("0x", "0X", "$"))
                    and all(c in "0123456789abcdefABCDEF"
                            for c in p.lstrip("$").removeprefix("0x").removeprefix("0X"))):
                return False
    # Disallow bare "-" (both sides empty).
    if all(not p for p in parts):
        return False
    return True


def parse_only_args(
    only: Optional[set[str]],
) -> tuple[Optional[set[str]], ScriptFilter]:
    """Split a comma-aggregated `--only` set into:

      * a section-level identifier set (existing semantics — fed to
        `driver._section_kinds_filter`)
      * a `ScriptFilter` carrying any block/window suffixes seen.

    The section identifier is always added to the section set, even when
    a block/window suffix is present, so non-script sections continue
    matching their existing `--only`-by-name path. Tokens with a block
    suffix that don't end up matching any script section produce no harm
    — the filter is consulted only when the script handler runs.
    """
    if not only:
        return None, ScriptFilter()
    section_ids: set[str] = set()
    sf = ScriptFilter()
    for tok in only:
        section, block, window = parse_only_token(tok)
        section_ids.add(section)
        if block is not None or window is not None:
            sf.add(ScriptTarget(
                section_id=section,
                block_range=block,
                window_range=window,
            ))
    return section_ids, sf

"""Parallelizable pre-encoding for heavy sections.

CPU-bound encoders (LZSS compress, script encode) run in a `ProcessPoolExecutor`
before the serial ROM-write phase. Each handler that opts in implements a
`prepare(section, files_root) -> bytes` function: pure, picklable, no shared
state. The build pipeline calls `parallel_prepare(spec, files_root)` first;
each section then gets a transient `_prepared` attr that handlers check before
re-doing the work.

Sections that mutate the working ROM (asar, project) can't be pre-prepared —
they're skipped here and run serially.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from retrotool.build.spec import Section, SectionKind


# ---- top-level prepare functions (picklable) -----------------------------

def _read_concat(section: Section, root: Path) -> bytes:
    chunks: list[bytes] = []
    for f in section.files:
        p = Path(str(f))
        if not p.is_absolute():
            p = (root / p).resolve()
        chunks.append(p.read_bytes())
    return b"".join(chunks)


def prepare_bin(section: Section, root: Path) -> bytes:
    """Return the bytes that will be written to ROM for a <bin> section.

    Handles concat + codec compression + size-padding. Mirrors `handle_bin`'s
    encode path but doesn't touch the ROM."""
    data = _read_concat(section, root)
    if section.codec:
        from retrotool.compression import registry as codec_registry
        codec = codec_registry.get(section.codec)
        data = codec.compress(data).data
    if section.size is not None:
        if len(data) > section.size:
            raise ValueError(
                f"{section.source}: <bin> data ({len(data)}b) exceeds "
                f"declared size ({section.size}b)"
            )
        data = data + b"\x00" * (section.size - len(data))
    return data


def prepare_graphics(section: Section, root: Path) -> bytes:
    """Pre-encode <graphics> bytes (raw passthrough today; bitplane reorder later)."""
    from retrotool.build.handlers import _BITPLANE_TRANSFORMS
    data = _read_concat(section, root)
    key = (section.codec or "").lower()
    if key not in _BITPLANE_TRANSFORMS:
        raise ValueError(
            f"{section.source}: bitplane transform encode={section.codec!r} "
            f"not yet implemented"
        )
    forward, _ = _BITPLANE_TRANSFORMS[key]
    return forward(data)


def prepare_script(section: Section, root: Path) -> bytes:
    """Pre-encode a <script> text file via its .tbl."""
    if section.table is None:
        raise ValueError(f"{section.source}: <script> requires table=…")
    from retrotool.script.table import Table
    tbl_path = Path(str(section.table))
    if not tbl_path.is_absolute():
        tbl_path = (root / tbl_path).resolve()
    text_path = Path(str(section.files[0]))
    if not text_path.is_absolute():
        text_path = (root / text_path).resolve()
    table = Table(tbl_path)
    text = text_path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln]
    return b"\x00".join(table.encode_text(ln) for ln in lines) + b"\x00"


PrepareFn = Callable[[Section, Path], bytes]

PREPARES: dict[SectionKind, PrepareFn] = {
    SectionKind.GRAPHICS: prepare_graphics,
    SectionKind.SCRIPT: prepare_script,
}


def _prepare_one(args: tuple[int, SectionKind, Section, Path]) -> tuple[int, bytes]:
    """Worker entry — returns (section_index, encoded_bytes)."""
    idx, kind, section, root = args
    if kind == SectionKind.BIN:
        # BIN is opt-in: only worth parallelizing when a codec is present.
        if not section.codec:
            return idx, b""
        return idx, prepare_bin(section, root)
    fn = PREPARES.get(kind)
    if fn is None:
        return idx, b""
    return idx, fn(section, root)


def parallel_prepare(
    spec, files_root: Path, *, max_workers: Optional[int] = None,
) -> int:
    """Pre-encode every prepare-eligible section in parallel.

    Mutates `section._prepared` (transient attr) on each section that produced
    bytes. Returns the number of sections prepared. Pass `max_workers=1` to
    force serial execution (useful for debugging). The default lets
    `ProcessPoolExecutor` choose."""
    tasks: list[tuple[int, SectionKind, Section, Path]] = []
    for i, s in enumerate(spec.sections):
        if s.kind == SectionKind.BIN and s.codec:
            tasks.append((i, s.kind, s, files_root))
        elif s.kind in PREPARES:
            tasks.append((i, s.kind, s, files_root))
    if not tasks:
        return 0
    if max_workers == 1:
        results = [_prepare_one(t) for t in tasks]
    else:
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            results = list(ex.map(_prepare_one, tasks))
    n = 0
    for idx, data in results:
        if data:
            spec.sections[idx]._prepared = data  # type: ignore[attr-defined]
            n += 1
    return n

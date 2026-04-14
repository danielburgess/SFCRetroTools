"""ROM region classifier. Aggregate heuristics into a region map."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RegionKind = Literal["code", "text", "graphics", "compressed", "pointer_table", "data", "unknown"]


@dataclass
class Region:
    offset: int
    length: int
    kind: RegionKind
    confidence: float = 0.0
    note: str = ""


def merge_regions(regions: list[Region], gap_tolerance: int = 0) -> list[Region]:
    """Merge adjacent regions of same kind, tolerating small gaps."""
    if not regions:
        return []
    srt = sorted(regions, key=lambda r: r.offset)
    out = [srt[0]]
    for r in srt[1:]:
        last = out[-1]
        if r.kind == last.kind and r.offset <= last.offset + last.length + gap_tolerance:
            new_end = max(last.offset + last.length, r.offset + r.length)
            out[-1] = Region(
                offset=last.offset,
                length=new_end - last.offset,
                kind=last.kind,
                confidence=max(last.confidence, r.confidence),
                note=last.note,
            )
        else:
            out.append(r)
    return out


def fill_gaps(regions: list[Region], rom_size: int, gap_kind: RegionKind = "unknown") -> list[Region]:
    """Insert placeholder regions covering gaps between classified regions."""
    srt = sorted(regions, key=lambda r: r.offset)
    out: list[Region] = []
    cursor = 0
    for r in srt:
        if r.offset > cursor:
            out.append(Region(offset=cursor, length=r.offset - cursor, kind=gap_kind))
        out.append(r)
        cursor = max(cursor, r.offset + r.length)
    if cursor < rom_size:
        out.append(Region(offset=cursor, length=rom_size - cursor, kind=gap_kind))
    return out

"""retrotool.heuristics — automated data structure discovery."""
from retrotool.heuristics.graphics import GraphicsCandidate, scan_graphics, shannon_entropy
from retrotool.heuristics.mapper import Region, RegionKind, fill_gaps, merge_regions
from retrotool.heuristics.pointers import PointerTableCandidate, scan_pointer_tables
from retrotool.heuristics.text import TextBlock, scan_text

__all__ = [
    "PointerTableCandidate",
    "scan_pointer_tables",
    "TextBlock",
    "scan_text",
    "GraphicsCandidate",
    "scan_graphics",
    "shannon_entropy",
    "Region",
    "RegionKind",
    "merge_regions",
    "fill_gaps",
]

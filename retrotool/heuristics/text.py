"""Text block detection. Find regions of printable bytes."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TextBlock:
    offset: int
    length: int
    printable_ratio: float


def scan_text(
    rom: bytes,
    min_length: int = 16,
    printable_range: tuple[int, int] = (0x20, 0x7F),
    terminators: tuple[int, ...] = (0x00, 0xFF),
    min_ratio: float = 0.85,
) -> list[TextBlock]:
    """Scan for runs of mostly-printable bytes separated by terminators."""
    out: list[TextBlock] = []
    lo, hi = printable_range
    start = None
    printable = 0
    for i, b in enumerate(rom):
        if lo <= b < hi:
            if start is None:
                start = i
                printable = 1
            else:
                printable += 1
        elif b in terminators and start is not None:
            length = i - start + 1
            if length >= min_length:
                ratio = printable / length
                if ratio >= min_ratio:
                    out.append(TextBlock(offset=start, length=length, printable_ratio=ratio))
            start = None
            printable = 0
        else:
            start = None
            printable = 0
    return out

"""ROM freespace manager. Tracks allocatable regions + records allocations."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FreeRegion:
    offset: int
    length: int

    @property
    def end(self) -> int:
        return self.offset + self.length


@dataclass
class Allocation:
    offset: int
    length: int
    tag: str = ""


class FreeSpace:
    """Maintains sorted list of free regions + allocation log."""

    def __init__(self, regions: list[FreeRegion] | None = None):
        self._free: list[FreeRegion] = sorted(regions or [], key=lambda r: r.offset)
        self.allocations: list[Allocation] = []

    def add_free(self, offset: int, length: int) -> None:
        self._free.append(FreeRegion(offset, length))
        self._coalesce()

    def allocate(self, length: int, tag: str = "", align: int = 1) -> int:
        """Return start offset of allocation. Raises if no fit."""
        for i, r in enumerate(self._free):
            aligned_start = (r.offset + align - 1) // align * align
            waste = aligned_start - r.offset
            if r.length - waste >= length:
                alloc = Allocation(offset=aligned_start, length=length, tag=tag)
                self.allocations.append(alloc)
                # Split region
                leading = FreeRegion(r.offset, waste) if waste > 0 else None
                trailing_off = aligned_start + length
                trailing_len = r.end - trailing_off
                trailing = FreeRegion(trailing_off, trailing_len) if trailing_len > 0 else None
                del self._free[i]
                if trailing:
                    self._free.insert(i, trailing)
                if leading:
                    self._free.insert(i, leading)
                return aligned_start
        raise RuntimeError(f"No free space for {length} bytes (align={align}) — used: {self.used_bytes}")

    @property
    def free_bytes(self) -> int:
        return sum(r.length for r in self._free)

    @property
    def used_bytes(self) -> int:
        return sum(a.length for a in self.allocations)

    def regions(self) -> list[FreeRegion]:
        return list(self._free)

    def _coalesce(self) -> None:
        self._free.sort(key=lambda r: r.offset)
        merged: list[FreeRegion] = []
        for r in self._free:
            if merged and r.offset <= merged[-1].end:
                last = merged[-1]
                new_end = max(last.end, r.end)
                merged[-1] = FreeRegion(last.offset, new_end - last.offset)
            else:
                merged.append(r)
        self._free = merged

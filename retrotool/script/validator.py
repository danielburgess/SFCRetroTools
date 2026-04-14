"""Round-trip + structural script validation."""
from __future__ import annotations

from dataclasses import dataclass, field

from retrotool.script.table import Table


@dataclass
class ValidationReport:
    total: int
    passed: int
    failures: list[tuple[int, str, str]] = field(default_factory=list)  # (index, original, roundtripped)

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        return f"Validation: {self.passed}/{self.total} passed ({len(self.failures)} failed)"


def round_trip(texts: list[str], table: Table, terminator: int = 0x00) -> ValidationReport:
    """Encode each text, decode back, compare. Terminator must round-trip cleanly."""
    failures = []
    passed = 0
    for i, t in enumerate(texts):
        try:
            enc = table.encode_text(t)
            dec = table.interpret_binary_data(list(enc), max_bytes=3)
            if dec == t:
                passed += 1
            else:
                failures.append((i, t, dec))
        except Exception as ex:
            failures.append((i, t, f"<exception: {ex!r}>"))
    return ValidationReport(total=len(texts), passed=passed, failures=failures)


def check_pointer_range(pointer_snes_addrs: list[int], start: int, end: int) -> list[int]:
    """Return indices of pointers outside [start, end)."""
    return [i for i, a in enumerate(pointer_snes_addrs) if not (start <= a < end)]


def check_max_length(texts: list[str], max_len: int) -> list[tuple[int, int]]:
    """Return (index, length) for strings exceeding max_len."""
    return [(i, len(t)) for i, t in enumerate(texts) if len(t) > max_len]

"""Python → 65816 ASM codegen helpers."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AsmLine:
    label: str = ""
    op: str = ""
    operand: str = ""
    comment: str = ""

    def render(self) -> str:
        parts = []
        if self.label:
            parts.append(f"{self.label}:")
        if self.op:
            instr = self.op if not self.operand else f"{self.op} {self.operand}"
            parts.append(f"    {instr}")
        if self.comment:
            parts.append(f"    ; {self.comment}")
        return '\n'.join(parts)


class AsmBuilder:
    def __init__(self):
        self.lines: list[AsmLine] = []

    def label(self, name: str) -> "AsmBuilder":
        self.lines.append(AsmLine(label=name))
        return self

    def instr(self, op: str, operand: str = "", comment: str = "") -> "AsmBuilder":
        self.lines.append(AsmLine(op=op, operand=operand, comment=comment))
        return self

    def comment(self, text: str) -> "AsmBuilder":
        self.lines.append(AsmLine(comment=text))
        return self

    def db(self, data: bytes, comment: str = "") -> "AsmBuilder":
        operand = ', '.join(f'${b:02X}' for b in data)
        self.lines.append(AsmLine(op="db", operand=operand, comment=comment))
        return self

    def render(self) -> str:
        return '\n'.join(line.render() for line in self.lines) + '\n'

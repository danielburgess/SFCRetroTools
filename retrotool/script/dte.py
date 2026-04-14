"""Dual-Tile Encoding (DTE) helper. Find highest-frequency digraphs + assign codes."""
from __future__ import annotations

from collections import Counter


def find_digraphs(texts: list[str], top_n: int = 128,
                  skip_chars: str = " \n\t") -> list[tuple[str, int]]:
    """Return top N digraphs by frequency. Skip digraphs containing `skip_chars`."""
    counter: Counter[str] = Counter()
    for t in texts:
        for i in range(len(t) - 1):
            pair = t[i:i + 2]
            if any(c in skip_chars for c in pair):
                continue
            counter[pair] += 1
    return counter.most_common(top_n)


def build_dte_table(texts: list[str], codes: list[int],
                    skip_chars: str = " \n\t") -> dict[str, int]:
    """Map top digraphs (by frequency) to the given code values."""
    pairs = find_digraphs(texts, top_n=len(codes), skip_chars=skip_chars)
    return {p[0]: c for p, c in zip(pairs, codes)}


def apply_dte(text: str, dte_map: dict[str, int]) -> str:
    """Rewrite `text`, replacing digraphs with their single-character DTE equivalents.

    DTE code is emitted as a bracketed [HH] literal for downstream Table.encode_text."""
    out = []
    i = 0
    while i < len(text):
        pair = text[i:i + 2]
        if len(pair) == 2 and pair in dte_map:
            out.append(f"[{dte_map[pair]:02X}]")
            i += 2
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


def savings_estimate(texts: list[str], dte_map: dict[str, int]) -> int:
    """Bytes saved by applying dte_map (each replaced digraph saves 1 byte)."""
    total = 0
    for t in texts:
        i = 0
        while i < len(t) - 1:
            if t[i:i + 2] in dte_map:
                total += 1
                i += 2
            else:
                i += 1
    return total

"""LZ4 frame compression via bundled lz4 CLI.

Deprecated once a pure-Python LZ4 impl lands at `retrotool.compression.lz4`.
"""
from __future__ import annotations

import subprocess

from retrotool import _toolchain


class Lz4Error(RuntimeError):
    """Raised when the lz4 binary exits non-zero."""


LZ4_FRAME_MAGIC = b"\x04\x22\x4d\x18"  # 0x184D2204 little-endian


def compress_lz4(data: bytes, *, level: int = 9) -> bytes:
    """Compress bytes → LZ4 frame bytes. `level` ∈ [1, 12]."""
    if not 1 <= level <= 12:
        raise ValueError(f"level must be 1..12, got {level}")
    cmd = [str(_toolchain.lz4()), f"-{level}", "-z", "-c", "-"]
    proc = subprocess.run(cmd, input=data, capture_output=True)
    if proc.returncode != 0:
        raise Lz4Error(
            f"lz4 compress failed ({proc.returncode})\n"
            f"--- stderr ---\n{proc.stderr.decode(errors='replace')}"
        )
    return proc.stdout


def decompress_lz4(data: bytes) -> bytes:
    """Decompress LZ4 frame bytes → raw bytes."""
    cmd = [str(_toolchain.lz4()), "-d", "-c", "-"]
    proc = subprocess.run(cmd, input=data, capture_output=True)
    if proc.returncode != 0:
        raise Lz4Error(
            f"lz4 decompress failed ({proc.returncode})\n"
            f"--- stderr ---\n{proc.stderr.decode(errors='replace')}"
        )
    return proc.stdout

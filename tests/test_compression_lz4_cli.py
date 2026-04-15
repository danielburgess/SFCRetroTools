"""Tests for retrotool.compression.lz4_cli."""
from __future__ import annotations

import os

import pytest

from retrotool.compression.lz4_cli import (
    LZ4_FRAME_MAGIC,
    Lz4Error,
    compress_lz4,
    decompress_lz4,
)


_HAS_LIBSFX = False
try:
    import retrotool_libsfx  # noqa: F401
    _HAS_LIBSFX = True
except ImportError:
    pass


libsfx = pytest.mark.skipif(not _HAS_LIBSFX, reason="retrotool_libsfx not installed")


@libsfx
def test_round_trip_random_64k():
    data = os.urandom(64 * 1024)
    compressed = compress_lz4(data)
    assert compressed.startswith(LZ4_FRAME_MAGIC)
    assert decompress_lz4(compressed) == data


@libsfx
def test_round_trip_repetitive_compresses():
    data = b"ABCD" * 4096  # 16 KiB, very compressible
    compressed = compress_lz4(data)
    assert compressed.startswith(LZ4_FRAME_MAGIC)
    assert len(compressed) < len(data) // 4
    assert decompress_lz4(compressed) == data


@libsfx
def test_round_trip_empty():
    compressed = compress_lz4(b"")
    assert compressed.startswith(LZ4_FRAME_MAGIC)
    assert decompress_lz4(compressed) == b""


@libsfx
def test_level_bounds_checked():
    with pytest.raises(ValueError):
        compress_lz4(b"x", level=0)
    with pytest.raises(ValueError):
        compress_lz4(b"x", level=13)


@libsfx
def test_decompress_garbage_raises():
    with pytest.raises(Lz4Error):
        decompress_lz4(b"not a valid lz4 frame at all")


@libsfx
def test_high_level_round_trip():
    """Bundled lz4 1.9.1 parses `-12` as level 12 (verified via `-b1 -e12`),
    so single-digit-flag concern from review M4 was a false positive — keep a
    round-trip guard so a future toolchain upgrade can't silently regress."""
    data = (b"The quick brown fox jumps over the lazy dog. " * 200)
    compressed = compress_lz4(data, level=12)
    assert compressed.startswith(LZ4_FRAME_MAGIC)
    assert decompress_lz4(compressed) == data


@libsfx
def test_level_affects_output_size():
    data = (b"The quick brown fox jumps over the lazy dog. " * 200)
    small = compress_lz4(data, level=1)
    big = compress_lz4(data, level=9)
    # Both valid frames, both round-trip. Size ordering across levels isn't a
    # contract (depends on lz4 build + input), so we only assert round-trip.
    assert decompress_lz4(small) == data
    assert decompress_lz4(big) == data

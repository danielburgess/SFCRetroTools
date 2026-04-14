"""Codec ABC + result types."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class DecompressResult:
    data: bytes
    consumed: int            # bytes consumed from input (including header)


@dataclass
class CompressResult:
    data: bytes
    original_size: int


class Codec(ABC):
    """Abstract compression codec."""

    name: str = ""

    @abstractmethod
    def decompress(self, data: bytes, offset: int = 0) -> DecompressResult: ...

    @abstractmethod
    def compress(self, data: bytes) -> CompressResult: ...

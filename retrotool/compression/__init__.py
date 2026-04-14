"""retrotool.compression — LZSS, RLE, detector, registry."""
from retrotool.compression.base import Codec, CompressResult, DecompressResult
from retrotool.compression.detector import CompressionCandidate, scan_lzss
from retrotool.compression.lzss import (
    LZSSCodec,
    LZSSParams,
    PARAMS_LEGACY,
    PARAMS_RBSHURA,
    PARAMS_ZAMN,
)
from retrotool.compression.registry import get, list_schemes, register
from retrotool.compression.rle import RLECodec, RLEParams

__all__ = [
    "Codec",
    "DecompressResult",
    "CompressResult",
    "LZSSCodec",
    "LZSSParams",
    "PARAMS_RBSHURA",
    "PARAMS_ZAMN",
    "PARAMS_LEGACY",
    "RLECodec",
    "RLEParams",
    "CompressionCandidate",
    "scan_lzss",
    "get",
    "register",
    "list_schemes",
]

"""retrotool.audio — SNES audio (BRR) codec wrappers."""
from retrotool.audio.brr import BrrError, decode_brr, encode_brr

__all__ = ["encode_brr", "decode_brr", "BrrError"]

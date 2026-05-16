"""LC_LZ2 codec tests — every command, every length-extension form, edge cases."""
from __future__ import annotations

import pytest

from retrotool.compression import get as get_codec
from retrotool.compression.lc_lz2 import (
    CMD_BYTE_FILL,
    CMD_DIRECT,
    CMD_INC_FILL,
    CMD_REPEAT,
    CMD_WORD_FILL,
    LCLZ2Codec,
)


def _hdr_5bit(cmd: int, length: int) -> bytes:
    """Encode a standard 5-bit-length header."""
    assert 1 <= length <= 32
    return bytes([(cmd << 5) | (length - 1)])


def _hdr_10bit(cmd: int, length: int) -> bytes:
    """Encode a 10-bit length-extension header (`111CCCLL  LLLLLLLL`)."""
    assert 1 <= length <= 1024
    ext = length - 1
    return bytes([0xE0 | ((cmd & 7) << 2) | ((ext >> 8) & 0x03), ext & 0xFF])


def _hdr_16bit(cmd: int, length: int) -> bytes:
    """Encode a 16-bit length-extension header (`110CCC00  LLHH  LLLL`)."""
    assert 1 <= length <= 65536
    ext = length - 1
    return bytes([0xC0 | ((cmd & 7) << 2), (ext >> 8) & 0xFF, ext & 0xFF])


# ---- decoder ------------------------------------------------------------


def test_decoder_terminator_alone():
    out = LCLZ2Codec().decompress(b"\xFF")
    assert out.data == b""
    assert out.consumed == 1


def test_decoder_direct_copy():
    payload = b"HELLO"
    blob = _hdr_5bit(CMD_DIRECT, len(payload)) + payload + b"\xFF"
    assert LCLZ2Codec().decompress(blob).data == payload


def test_decoder_byte_fill():
    blob = _hdr_5bit(CMD_BYTE_FILL, 8) + b"\xAA" + b"\xFF"
    assert LCLZ2Codec().decompress(blob).data == b"\xAA" * 8


def test_decoder_word_fill():
    blob = _hdr_5bit(CMD_WORD_FILL, 5) + b"\x12\x34" + b"\xFF"
    # 5 bytes alternating 12 34 12 34 12.
    assert LCLZ2Codec().decompress(blob).data == b"\x12\x34\x12\x34\x12"


def test_decoder_increasing_fill():
    blob = _hdr_5bit(CMD_INC_FILL, 6) + b"\xFE" + b"\xFF"
    # 0xFE 0xFF 0x00 0x01 0x02 0x03 — wraps at 0x100.
    assert LCLZ2Codec().decompress(blob).data == b"\xFE\xFF\x00\x01\x02\x03"


def test_decoder_repeat_no_overlap():
    # First emit a literal "ABCD", then repeat 4 bytes from address 0.
    blob = (
        _hdr_5bit(CMD_DIRECT, 4) + b"ABCD"
        + _hdr_5bit(CMD_REPEAT, 4) + b"\x00\x00"  # addr 0x0000
        + b"\xFF"
    )
    assert LCLZ2Codec().decompress(blob).data == b"ABCDABCD"


def test_decoder_repeat_self_overlap():
    """Source range can extend into bytes we haven't written yet — the
    output should grow byte-by-byte so the just-emitted tail is readable."""
    blob = (
        _hdr_5bit(CMD_DIRECT, 1) + b"X"
        + _hdr_5bit(CMD_REPEAT, 6) + b"\x00\x00"  # repeat 6 from addr 0 — but only 1 byte exists
        + b"\xFF"
    )
    # Each step reads one byte and writes one — classic LZ "fill" pattern.
    assert LCLZ2Codec().decompress(blob).data == b"XXXXXXX"


def test_decoder_10bit_length_extension():
    """10-bit form covers lengths 33..1024."""
    payload = b"\x55" * 100
    blob = _hdr_10bit(CMD_BYTE_FILL, 100) + b"\x55" + b"\xFF"
    assert LCLZ2Codec().decompress(blob).data == payload


def test_decoder_16bit_length_extension():
    """16-bit form needed only for lengths >1024."""
    blob = _hdr_16bit(CMD_BYTE_FILL, 2048) + b"\xCC" + b"\xFF"
    assert LCLZ2Codec().decompress(blob).data == b"\xCC" * 2048


def test_decoder_truncated_payload_raises():
    # 5-bit direct copy claiming 8 bytes, only 3 supplied.
    with pytest.raises(ValueError, match="truncated"):
        LCLZ2Codec().decompress(_hdr_5bit(CMD_DIRECT, 8) + b"ABC")


def test_decoder_truncated_10bit_header_raises():
    # First byte starts a 10-bit extension but second byte is missing.
    with pytest.raises(ValueError, match="truncated 10-bit length header"):
        LCLZ2Codec().decompress(bytes([0xE0]))


# ---- encoder round-trip --------------------------------------------------


@pytest.mark.parametrize("data", [
    b"",                                # empty
    b"A",                               # single byte
    b"\x00" * 32,                       # exact 5-bit max
    b"\x00" * 33,                       # one over → 10-bit
    b"\x00" * 1024,                     # exact 10-bit max
    b"\x00" * 1025,                     # one over → 16-bit
    b"AB" * 100,                        # word-fill candidate
    bytes(range(256)),                  # increasing-fill candidate (one cycle)
    b"\x55" * 5000,                     # large run, exercises 16-bit
    b"The quick brown fox jumps over the lazy dog." * 50,  # mixed/repeat-heavy
])
def test_round_trip(data):
    codec = LCLZ2Codec()
    cz = codec.compress(data)
    dz = codec.decompress(cz.data)
    assert dz.data == data
    if data:
        # Compressed form should at least include the terminator and a header.
        assert cz.data[-1] == 0xFF


def test_round_trip_random_bytes():
    """Random data is incompressible; round-trip must still work cleanly."""
    import random
    random.seed(0xC001D00D)
    data = bytes(random.randint(0, 255) for _ in range(2048))
    codec = LCLZ2Codec()
    assert codec.decompress(codec.compress(data).data).data == data


# ---- registry wiring ----------------------------------------------------


def test_registry_lookup_returns_lc_lz2():
    codec = get_codec("lc-lz2")
    assert isinstance(codec, LCLZ2Codec)
    # Spot-check: round-trip works through the registry-returned instance too.
    payload = b"REGISTRY-WORKS" * 8
    assert codec.decompress(codec.compress(payload).data).data == payload

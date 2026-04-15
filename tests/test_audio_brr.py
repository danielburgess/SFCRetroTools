"""Tests for retrotool.audio.brr — BRRtools wrappers."""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from retrotool.audio.brr import BrrError, decode_brr, encode_brr


_HAS_LIBSFX = False
try:
    import retrotool_libsfx  # noqa: F401
    _HAS_LIBSFX = True
except ImportError:
    pass


libsfx = pytest.mark.skipif(not _HAS_LIBSFX, reason="retrotool_libsfx not installed")


def _write_sine_wav(path: Path, *, seconds: float = 0.5, freq: int = 440,
                    rate: int = 32000, amp: float = 0.6) -> Path:
    """Mono 16-bit PCM WAV of a pure sine tone."""
    n_samples = int(seconds * rate)
    frames = bytearray()
    max_int = 32760
    for i in range(n_samples):
        val = int(amp * max_int * math.sin(2 * math.pi * freq * i / rate))
        frames += struct.pack("<h", val)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


@libsfx
def test_encode_brr_produces_file(tmp_path):
    wav = _write_sine_wav(tmp_path / "sine.wav")
    brr = encode_brr(wav, tmp_path / "sine.brr")
    assert brr.exists()
    assert brr.stat().st_size > 0
    # BRR block = 9 bytes. File size is a multiple of 9.
    assert brr.stat().st_size % 9 == 0


@libsfx
def test_encode_brr_block_count_matches_samples(tmp_path):
    # 0.5s at 32kHz = 16000 samples. BRR packs 16 samples/block → 1000 blocks.
    wav = _write_sine_wav(tmp_path / "sine.wav", seconds=0.5, rate=32000)
    brr = encode_brr(wav, tmp_path / "sine.brr")
    blocks = brr.stat().st_size // 9
    # BRRtools may add a wrap block; allow ±2 off from 1000.
    assert 998 <= blocks <= 1002


@libsfx
def test_round_trip_brr_wav(tmp_path):
    wav = _write_sine_wav(tmp_path / "sine.wav", seconds=0.25)
    brr = encode_brr(wav, tmp_path / "sine.brr")
    out = decode_brr(brr, tmp_path / "decoded.wav", sample_rate=32000)
    assert out.exists()
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 32000
        assert w.getsampwidth() == 2
        assert w.getnframes() > 0


@libsfx
def test_encode_with_loop_point(tmp_path):
    wav = _write_sine_wav(tmp_path / "sine.wav")
    brr = encode_brr(wav, tmp_path / "looped.brr", loop_point=0)
    assert brr.exists()
    # First block's header byte: bit 1 = loop flag. Check at least one
    # header has it set.
    data = brr.read_bytes()
    loop_flags = [data[i] & 0x02 for i in range(0, len(data), 9)]
    assert any(loop_flags)


@libsfx
def test_encode_rejects_both_resample_modes(tmp_path):
    wav = _write_sine_wav(tmp_path / "sine.wav")
    with pytest.raises(ValueError):
        encode_brr(wav, tmp_path / "out.brr",
                   resample_rate=1.0, target_samplerate=22050)


@libsfx
def test_encode_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        encode_brr(tmp_path / "nope.wav", tmp_path / "out.brr")


@libsfx
def test_decode_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        decode_brr(tmp_path / "nope.brr", tmp_path / "out.wav")


@libsfx
def test_encode_error_raises_brr_error(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav file")
    with pytest.raises(BrrError):
        encode_brr(bad, tmp_path / "out.brr")

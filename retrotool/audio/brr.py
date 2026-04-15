"""BRR encoder/decoder wrappers (BRRtools 3.15)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from retrotool import _toolchain


class BrrError(RuntimeError):
    """Raised when brr_encoder/brr_decoder exits non-zero."""


def encode_brr(
    wav: Path,
    out_brr: Path,
    *,
    loop_point: int | None = None,
    resample_rate: float | None = None,
    target_samplerate: int | None = None,
    gauss_boost: bool = False,
    amplitude: float | None = None,
    no_wrap: bool = False,
) -> Path:
    """Encode WAV → BRR.

    - `loop_point`: sample index where loop starts (enables loop flag).
      Pass 0 to loop from beginning, a positive int to loop from there,
      or None to disable looping.
    - `resample_rate`: ratio < 1.0 = upsample (better quality, bigger),
      > 1.0 = downsample. Uses bandlimited interp (`-rb`).
    - `target_samplerate`: alternative to `resample_rate` — resample to an
      exact rate via bandlimited interp (`-sb`). Mutually exclusive.
    - `gauss_boost`: compensate SNES hardware gaussian filter (`-g`).
    - `amplitude`: multiply input by this factor.
    - `no_wrap`: disable wrapping (old SPC player compat).
    """
    if resample_rate is not None and target_samplerate is not None:
        raise ValueError("pass resample_rate OR target_samplerate, not both")

    wav = Path(wav)
    out_brr = Path(out_brr)
    if not wav.exists():
        raise FileNotFoundError(wav)
    out_brr.parent.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [str(_toolchain.brr_encoder())]
    if amplitude is not None:
        cmd.append(f"-a{amplitude}")
    if loop_point is not None:
        cmd.append(f"-l{loop_point}")
    if resample_rate is not None:
        cmd.append(f"-rb{resample_rate}")
    if target_samplerate is not None:
        cmd.append(f"-sb{target_samplerate}")
    if no_wrap:
        cmd.append("-w")
    if gauss_boost:
        cmd.append("-g")
    cmd += [str(wav), str(out_brr)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BrrError(
            f"brr_encoder failed ({proc.returncode})\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return out_brr


def decode_brr(
    brr: Path,
    out_wav: Path,
    *,
    sample_rate: int = 32000,
    loop_count: int = 1,
    loop_start_block: int = 0,
    min_seconds: float | None = None,
    gauss_filter: bool = False,
) -> Path:
    """Decode BRR → WAV.

    - `loop_count`: number of times to loop through the sample (default 1).
    - `loop_start_block`: where the loop begins, in BRR blocks.
    - `min_seconds`: pad output to at least this duration (needs loop_count>1).
    - `gauss_filter`: apply SNES gaussian lowpass.
    """
    brr = Path(brr)
    out_wav = Path(out_wav)
    if not brr.exists():
        raise FileNotFoundError(brr)
    out_wav.parent.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [
        str(_toolchain.brr_decoder()),
        f"-n{loop_count}", f"-l{loop_start_block}", f"-s{sample_rate}",
    ]
    if min_seconds is not None:
        cmd.append(f"-m{min_seconds}")
    if gauss_filter:
        cmd.append("-g")
    cmd += [str(brr), str(out_wav)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BrrError(
            f"brr_decoder failed ({proc.returncode})\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return out_wav

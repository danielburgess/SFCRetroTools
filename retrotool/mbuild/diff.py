"""Diff writers: IPS (pure-python) + xdelta3 (subprocess).

Consumed by `build()` post-process when `spec.diff` is set. Returned paths
live next to the built ROM (e.g. `out.sfc.ips`, `out.sfc.xdelta`).
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


IPS_MAX_SIZE = 0x1000000          # 16 MB — IPS 24-bit offset limit
IPS_HEADER = b"PATCH"
IPS_EOF = b"EOF"
IPS_EOF_OFFSET = 0x454F46         # b"EOF" as BE int — forbidden as a record offset


class DiffError(RuntimeError):
    pass


@dataclass
class DiffResult:
    path: Path
    format: str           # "ips" | "xdelta"
    size: int
    skipped: bool = False # True if graceful-skip fallback hit
    note: str = ""


# ---- IPS ------------------------------------------------------------------

def _encode_ips_runs(original: bytes, modified: bytes) -> list[tuple[int, bytes]]:
    """Walk both buffers; emit (offset, bytes) records for every differing run.

    `modified` may be longer than `original` — trailing new bytes become a
    single record at `len(original)`. Runs are kept contiguous (we don't split
    at zero bytes); RLE compression happens in `_pack_ips`."""
    runs: list[tuple[int, bytes]] = []
    n = min(len(original), len(modified))
    i = 0
    while i < n:
        if original[i] == modified[i]:
            i += 1
            continue
        start = i
        while i < n and original[i] != modified[i]:
            i += 1
        runs.append((start, modified[start:i]))
    if len(modified) > len(original):
        runs.append((len(original), modified[len(original):]))
    return runs


def _pack_ips_record(offset: int, data: bytes) -> bytes:
    """Split `data` into IPS records. RLE if a run of ≥13 equal bytes (threshold
    where a 3-byte RLE record beats a 2+N raw record)."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        # Try RLE.
        rle_len = 1
        while i + rle_len < n and data[i + rle_len] == data[i] and rle_len < 0xFFFF:
            rle_len += 1
        if rle_len >= 13:
            rec_off = offset + i
            if rec_off == IPS_EOF_OFFSET:
                # Shift one byte back: emit the single byte as a raw record,
                # let the RLE start one byte later. Simple but reliable.
                out += _raw_record(rec_off, data[i:i + 1])
                i += 1
                continue
            out += rec_off.to_bytes(3, "big")
            out += (0).to_bytes(2, "big")          # length=0 → RLE
            out += rle_len.to_bytes(2, "big")
            out += bytes([data[i]])
            i += rle_len
            continue

        # Raw record: grow until next useful RLE opportunity or end.
        raw_start = i
        while i < n and i - raw_start < 0xFFFF:
            # Lookahead: if next 13 bytes are equal, bail out to RLE.
            if i + 13 <= n and all(data[i + k] == data[i] for k in range(13)):
                break
            i += 1
        rec_off = offset + raw_start
        segment = data[raw_start:i]
        if rec_off == IPS_EOF_OFFSET:
            # Write the first byte as a raw 1-byte record at a shifted offset
            # pair (not possible — offset is fixed). Instead, split: one byte
            # raw at rec_off+1 isn't valid either. Cheapest correct workaround:
            # emit one byte earlier as RLE of length 1? Also invalid (RLE ≥2).
            # Just make the raw record span a different offset by prepending
            # the previous byte — but we don't have the original context here.
            # This is a degenerate edge case: a change spanning exactly byte
            # 0x454F46. We surface a clear error; ROMs avoiding that offset are
            # fine.
            raise DiffError(
                f"IPS diff record would collide with EOF sentinel offset "
                f"{IPS_EOF_OFFSET:#x}"
            )
        out += _raw_record(rec_off, segment)
    return bytes(out)


def _raw_record(offset: int, data: bytes) -> bytes:
    return (
        offset.to_bytes(3, "big")
        + len(data).to_bytes(2, "big")
        + data
    )


def write_ips(original: bytes, modified: bytes, out_path: Path) -> DiffResult:
    """Write an IPS patch from `original` → `modified` to `out_path`."""
    if len(modified) > IPS_MAX_SIZE:
        raise DiffError(
            f"IPS patch source size {len(modified):#x} exceeds 16MB limit "
            f"({IPS_MAX_SIZE:#x}). Use xdelta for larger ROMs."
        )
    runs = _encode_ips_runs(original, modified)
    body = bytearray(IPS_HEADER)
    for off, chunk in runs:
        body += _pack_ips_record(off, chunk)
    body += IPS_EOF
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(body))
    return DiffResult(path=out_path, format="ips", size=len(body))


def apply_ips(patch: bytes, original: bytes) -> bytes:
    """Apply an IPS patch (used in tests to verify round-trip)."""
    if patch[:5] != IPS_HEADER:
        raise DiffError("not an IPS patch")
    out = bytearray(original)
    i = 5
    while True:
        if patch[i:i + 3] == IPS_EOF:
            break
        offset = int.from_bytes(patch[i:i + 3], "big")
        length = int.from_bytes(patch[i + 3:i + 5], "big")
        i += 5
        if length == 0:  # RLE
            rle_len = int.from_bytes(patch[i:i + 2], "big")
            value = patch[i + 2]
            i += 3
            end = offset + rle_len
            if end > len(out):
                out.extend(b"\x00" * (end - len(out)))
            for k in range(rle_len):
                out[offset + k] = value
        else:
            end = offset + length
            if end > len(out):
                out.extend(b"\x00" * (end - len(out)))
            out[offset:end] = patch[i:i + length]
            i += length
    return bytes(out)


# ---- xdelta3 --------------------------------------------------------------

def _xdelta_bundled() -> Optional[str]:
    """Resolve the bundled `retrotool-xdelta` binary, if that wheel is installed."""
    try:
        from retrotool_xdelta import xdelta3_binary, ToolNotBundledError
    except ImportError:
        return None
    try:
        return str(xdelta3_binary())
    except ToolNotBundledError:
        return None


def _xdelta_cmd() -> Optional[str]:
    """Prefer bundled wheel, else fall back to system `xdelta3` on PATH."""
    return _xdelta_bundled() or shutil.which("xdelta3")


def xdelta_available() -> bool:
    return _xdelta_cmd() is not None


def write_xdelta(
    original_path: Path,
    modified_path: Path,
    out_path: Path,
    *,
    required: bool = False,
) -> DiffResult:
    """Create an xdelta3 patch via the bundled `retrotool-xdelta` wheel or a
    system `xdelta3`. If neither is available and `required=False`, return a
    skipped DiffResult with an install hint; else raise DiffError."""
    binary = _xdelta_cmd()
    if binary is None:
        msg = (
            "xdelta3 unavailable — install `retrotool-xdelta` (bundled) "
            "or put `xdelta3` on PATH to enable xdelta diffs"
        )
        if required:
            raise DiffError(msg)
        return DiffResult(path=out_path, format="xdelta", size=0, skipped=True, note=msg)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        binary, "-e", "-f",
        "-s", str(original_path),
        str(modified_path),
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise DiffError(
            f"xdelta3 failed ({proc.returncode})\n--- stderr ---\n{proc.stderr}"
        )
    return DiffResult(path=out_path, format="xdelta", size=out_path.stat().st_size)


# ---- driver ---------------------------------------------------------------

def write_diff(
    kind: str,
    *,
    original_path: Path,
    modified_path: Path,
    out_path: Optional[Path] = None,
) -> DiffResult:
    """Dispatch to the right writer. `kind` ∈ {"ips","xdelta"}.

    `out_path` defaults to `<modified_path>.<kind>`."""
    kind = kind.lower()
    if out_path is None:
        out_path = modified_path.with_suffix(modified_path.suffix + f".{kind}")
    if kind == "ips":
        return write_ips(original_path.read_bytes(), modified_path.read_bytes(), out_path)
    if kind == "xdelta":
        return write_xdelta(original_path, modified_path, out_path)
    raise DiffError(f"unknown diff format: {kind!r} (expected 'ips' or 'xdelta')")

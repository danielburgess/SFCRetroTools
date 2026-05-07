"""Build progress reporter — animated TTY braille spinner + non-TTY log fallback.

The driver fires lifecycle events through a `Reporter`; the rendering choice
lives here. All methods are safe to call from worker threads (BrailleReporter
serializes via a Lock; the spinner thread paints on a fixed cadence).

Design:
- Each section gets a row. Rows pass through statuses:
  QUEUED → GATHER (worker computing) → APPLY (main loop applying writes)
  → terminal (DONE / CACHE_HIT / SKIPPED / ERROR).
- BrailleReporter keeps a live region at the bottom of the terminal: a
  divider line plus one row per actively-running section, with the active
  row's status glyph cycling through `_BRAILLE` at ~12.5 Hz. As sections
  reach a terminal state they scroll up out of the live region as static
  log lines (in completion order; parallel sections finish out-of-order).
- LogReporter prints one line per state transition — grep-friendly, used
  on non-TTY (CI / piped output / log redirect) or `--no-progress`.
"""
from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, TextIO


# Standard Linux braille spinner. 10 frames at 12.5 Hz = visible cycle ~0.8s.
_BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# ANSI control sequences for flicker-free animated paint. Synchronized Output
# Mode (DEC Private Mode 2026) tells the terminal to defer display until the
# end marker — supporting terminals (kitty, iTerm2, wezterm, Alacritty,
# recent xterm/mintty) render frames atomically; non-supporting terminals
# ignore the unknown CSI and we still benefit from the single buffered write.
_SYNC_BEGIN  = "\033[?2026h"
_SYNC_END    = "\033[?2026l"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"


class SectionStatus(str, Enum):
    QUEUED       = "queued"
    GATHER       = "gather"        # worker thread is computing writes
    GATHER_DONE  = "gather_done"   # worker returned; awaiting main-thread apply
    APPLY        = "apply"         # main loop applying writes / running serial handler
    DONE         = "done"
    CACHE_HIT    = "cache_hit"
    SKIPPED      = "skipped"
    ERROR        = "error"


_TERMINAL = {SectionStatus.DONE, SectionStatus.CACHE_HIT,
             SectionStatus.SKIPPED, SectionStatus.ERROR}

# Statuses that should NOT appear in the live "active" region: queued (worker
# hasn't picked it up yet), gather_done (worker returned but main hasn't
# drained the future), and any terminal status (already scrolled to history).
_HIDDEN_FROM_ACTIVE = (
    _TERMINAL | {SectionStatus.QUEUED, SectionStatus.GATHER_DONE}
)


@dataclass
class _SectionState:
    idx: int
    label: str
    kind: str
    status: SectionStatus = SectionStatus.QUEUED
    started_at: float = 0.0     # perf_counter when first non-QUEUED state hit
    elapsed_ms: int = 0
    bytes_written: int = 0
    note: str = ""


class Reporter:
    """No-op base; thread-safe by contract. Used as `with reporter: ...`."""

    def __enter__(self) -> "Reporter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Ensure background threads (if any) shut down even on exception.
        if exc_type is not None and not getattr(self, "_done_called", False):
            self.build_done(ok=False, summary=f"build failed: {exc}")

    def build_started(self, total_sections: int) -> None: ...
    def section_queued(self, idx: int, label: str, kind: str) -> None: ...
    def section_status(self, idx: int, status: SectionStatus, *,
                       note: str = "", bytes_written: int = 0) -> None: ...
    def build_done(self, ok: bool, summary: str) -> None:
        self._done_called = True


class LogReporter(Reporter):
    """One stderr line per terminal state — grep-friendly, no animation."""

    def __init__(self, stream: TextIO = sys.stderr):
        self._stream = stream
        self._lock = threading.Lock()
        self._states: dict[int, _SectionState] = {}
        self._t0 = 0.0

    def build_started(self, total_sections: int) -> None:
        with self._lock:
            self._t0 = time.perf_counter()
            self._stream.write(f"build: {total_sections} section(s)\n")
            self._stream.flush()

    def section_queued(self, idx: int, label: str, kind: str) -> None:
        with self._lock:
            self._states[idx] = _SectionState(idx=idx, label=label, kind=kind)

    def section_status(self, idx: int, status: SectionStatus, *,
                       note: str = "", bytes_written: int = 0) -> None:
        with self._lock:
            st = self._states.get(idx)
            if st is None:
                return
            now = time.perf_counter()
            if status in (SectionStatus.GATHER, SectionStatus.APPLY) and not st.started_at:
                st.started_at = now
            # Freeze worker-time at GATHER_DONE so terminal display shows
            # actual compute time, not compute + drain-wait.
            if status == SectionStatus.GATHER_DONE and st.started_at and not st.elapsed_ms:
                st.elapsed_ms = int((now - st.started_at) * 1000)
            st.status = status
            st.note = note
            if status in _TERMINAL:
                if st.started_at and not st.elapsed_ms:
                    st.elapsed_ms = int((now - st.started_at) * 1000)
                st.bytes_written = bytes_written
                self._stream.write(_format_log_line(st) + "\n")
                self._stream.flush()

    def build_done(self, ok: bool, summary: str) -> None:
        super().build_done(ok, summary)
        with self._lock:
            self._stream.write(summary + "\n")
            self._stream.flush()


class BrailleReporter(Reporter):
    """Animated TTY reporter. Live region at bottom; finalized rows scroll up."""

    FRAME_MS = 80  # 12.5 Hz
    MAX_LIVE_ROWS = 12

    def __init__(self, stream: TextIO = sys.stderr):
        self._stream = stream
        self._lock = threading.Lock()
        self._states: dict[int, _SectionState] = {}
        self._order: list[int] = []
        self._finalized_pending: list[int] = []
        self._last_live_lines = 0
        self._frame = 0
        self._total = 0
        self._t0 = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def build_started(self, total_sections: int) -> None:
        with self._lock:
            self._total = total_sections
            self._t0 = time.perf_counter()
        self._stream.write(_HIDE_CURSOR)
        self._stream.flush()
        self._thread = threading.Thread(
            target=self._tick, daemon=True, name="retrotool-progress",
        )
        self._thread.start()

    def section_queued(self, idx: int, label: str, kind: str) -> None:
        with self._lock:
            if idx not in self._states:
                st = _SectionState(idx=idx, label=label, kind=kind)
                self._states[idx] = st
                self._order.append(idx)

    def section_status(self, idx: int, status: SectionStatus, *,
                       note: str = "", bytes_written: int = 0) -> None:
        with self._lock:
            st = self._states.get(idx)
            if st is None:
                return
            now = time.perf_counter()
            if status in (SectionStatus.GATHER, SectionStatus.APPLY) and not st.started_at:
                st.started_at = now
            # Freeze worker-time at GATHER_DONE so the terminal row shows
            # actual compute time, not compute + drain-wait. Also stops the
            # active-row timer ticking after the worker is finished.
            if status == SectionStatus.GATHER_DONE and st.started_at and not st.elapsed_ms:
                st.elapsed_ms = int((now - st.started_at) * 1000)
            st.status = status
            st.note = note
            if status in _TERMINAL:
                if st.started_at and not st.elapsed_ms:
                    st.elapsed_ms = int((now - st.started_at) * 1000)
                st.bytes_written = bytes_written
                self._finalized_pending.append(idx)

    def _tick(self) -> None:
        while not self._stop.is_set():
            try:
                self._paint()
            except Exception:
                pass  # tick thread must never crash the build
            self._stop.wait(self.FRAME_MS / 1000.0)

    def _paint(self) -> None:
        with self._lock:
            self._frame += 1
            self._render_locked()

    def _render_locked(self) -> None:
        # Compose the entire frame into one buffer, wrapped in synchronized
        # output mode, then emit with a single write+flush. Atomic on
        # supporting terminals; greatly reduced tear on the rest.
        buf: list[str] = [_SYNC_BEGIN]
        # Erase prior live region (move up + clear-to-end-of-screen).
        if self._last_live_lines > 0:
            buf.append(f"\033[{self._last_live_lines}A\033[J")
        # Drain finalized rows above the live region.
        while self._finalized_pending:
            idx = self._finalized_pending.pop(0)
            st = self._states[idx]
            buf.append(_format_finalized(st) + "\n")
        # Active rows.
        active = [
            self._states[i] for i in self._order
            if self._states[i].status not in _HIDDEN_FROM_ACTIVE
        ]
        done_count = sum(1 for s in self._states.values() if s.status in _TERMINAL)
        elapsed = time.perf_counter() - self._t0
        spin = _BRAILLE[self._frame % len(_BRAILLE)]
        divider = (
            f"\033[2m──\033[0m \033[36m{spin}\033[0m "
            f"\033[1m{done_count}/{self._total}\033[0m "
            f"\033[2m· {elapsed:5.1f}s · {len(active)} active\033[0m"
        )
        buf.append(divider + "\n")
        for st in active[:self.MAX_LIVE_ROWS]:
            buf.append(_format_active(st, self._frame) + "\n")
        if len(active) > self.MAX_LIVE_ROWS:
            buf.append(f"\033[2m  … {len(active) - self.MAX_LIVE_ROWS} more\033[0m\n")
            self._last_live_lines = 1 + self.MAX_LIVE_ROWS + 1
        else:
            self._last_live_lines = 1 + len(active)
        buf.append(_SYNC_END)
        self._stream.write("".join(buf))
        self._stream.flush()

    def build_done(self, ok: bool, summary: str) -> None:
        super().build_done(ok, summary)
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
        try:
            with self._lock:
                buf: list[str] = [_SYNC_BEGIN]
                if self._last_live_lines > 0:
                    buf.append(f"\033[{self._last_live_lines}A\033[J")
                while self._finalized_pending:
                    idx = self._finalized_pending.pop(0)
                    buf.append(_format_finalized(self._states[idx]) + "\n")
                self._last_live_lines = 0
                buf.append(summary + "\n")
                buf.append(_SYNC_END)
                self._stream.write("".join(buf))
                self._stream.flush()
        finally:
            # Always restore the cursor — even if the final paint raises,
            # we must not leave the user's terminal with the cursor hidden.
            self._stream.write(_SHOW_CURSOR)
            self._stream.flush()


def _format_size(n: int) -> str:
    if n < 1024:
        return f"{n}b"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"


def _format_active(st: _SectionState, frame: int) -> str:
    spin = _BRAILLE[(frame + st.idx) % len(_BRAILLE)]
    if st.elapsed_ms:
        elapsed_ms = st.elapsed_ms
    elif st.started_at:
        elapsed_ms = int((time.perf_counter() - st.started_at) * 1000)
    else:
        elapsed_ms = 0
    verb = {
        SectionStatus.GATHER: "gathering",
        SectionStatus.APPLY:  "applying ",
        SectionStatus.QUEUED: "queued   ",
    }.get(st.status, "         ")
    label = (st.label[:46] + "…") if len(st.label) > 47 else st.label
    return (
        f"  \033[36m{spin}\033[0m \033[2m{st.kind:>14s}\033[0m  "
        f"{label:<47s}  "
        f"\033[2m{elapsed_ms:>5d}ms · {verb}\033[0m"
    )


def _format_finalized(st: _SectionState) -> str:
    label = (st.label[:46] + "…") if len(st.label) > 47 else st.label
    if st.status == SectionStatus.CACHE_HIT:
        return (
            f"  \033[33m⚡\033[0m \033[2m{st.kind:>14s}\033[0m  "
            f"{label:<47s}  \033[33mcached\033[0m"
        )
    if st.status == SectionStatus.SKIPPED:
        note = f" ({st.note})" if st.note else ""
        return (
            f"  \033[2m·\033[0m \033[2m{st.kind:>14s}\033[0m  "
            f"{label:<47s}  \033[2mskipped{note}\033[0m"
        )
    if st.status == SectionStatus.ERROR:
        msg = (st.note[:60] + "…") if len(st.note) > 61 else st.note
        return (
            f"  \033[31m✗\033[0m \033[31;1m{st.kind:>14s}\033[0m  "
            f"{label:<47s}  \033[31m{msg}\033[0m"
        )
    size = _format_size(st.bytes_written)
    return (
        f"  \033[32m✓\033[0m \033[2m{st.kind:>14s}\033[0m  "
        f"{label:<47s}  "
        f"\033[2m{st.elapsed_ms:>5d}ms · {size:>8s}\033[0m"
    )


def _format_log_line(st: _SectionState) -> str:
    glyph = {
        SectionStatus.DONE:      "DONE  ",
        SectionStatus.CACHE_HIT: "CACHE ",
        SectionStatus.SKIPPED:   "SKIP  ",
        SectionStatus.ERROR:     "ERROR ",
    }.get(st.status, "      ")
    base = f"  {glyph} {st.kind:>14s}  {st.label}"
    if st.status == SectionStatus.DONE:
        base += f"  ({st.elapsed_ms}ms, {_format_size(st.bytes_written)})"
    elif st.status == SectionStatus.ERROR and st.note:
        base += f"  — {st.note}"
    elif st.status == SectionStatus.SKIPPED and st.note:
        base += f"  — {st.note}"
    return base


def make_reporter(*, animate: Optional[bool] = None,
                  stream: TextIO = sys.stderr) -> Reporter:
    """TTY → BrailleReporter, else LogReporter. Override with `animate=`."""
    if animate is None:
        animate = stream.isatty()
    return BrailleReporter(stream) if animate else LogReporter(stream)

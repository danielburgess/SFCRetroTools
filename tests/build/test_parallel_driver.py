"""Driver tests for the gather-then-apply ThreadPool path + reporter events.

What we verify:
  * Output bytes are identical between serial (`parallel=1`) and parallel
    (`parallel=8`) execution. Determinism is preserved by applying writes in
    declared section order regardless of worker completion order.
  * Mixed parallel + serial + cache-hit sequences hit the right code paths.
  * The Reporter receives a coherent event sequence (build_started, per-section
    queued/status transitions, build_done) and bytes_written totals match
    the actual writes.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import pytest

from retrotool.build import BuildSpec, Section, SectionKind, build
from retrotool.build.reporter import Reporter, SectionStatus


# ---- shared fixture -------------------------------------------------------

_ROM_SIZE = 0x80_000


def _make_lorom(tmp_path: Path) -> Path:
    body = bytearray([0x00] * _ROM_SIZE)
    body[0x7FC0:0x7FC0 + 21] = b"TEST ROM             "
    body[0x7FD5] = 0x20
    body[0x7FD7] = 0x09
    body[0x7FD9] = 0x01
    body[0x7FDA] = 0x33
    body[0x7FDC:0x7FE0] = b"\xFF\xFF\x00\x00"
    csum = sum(body) & 0xFFFF
    comp = csum ^ 0xFFFF
    body[0x7FDC:0x7FE0] = bytes([
        comp & 0xFF, (comp >> 8) & 0xFF, csum & 0xFF, (csum >> 8) & 0xFF,
    ])
    p = tmp_path / "base.sfc"
    p.write_bytes(body)
    return p


def _many_section_spec(tmp_path: Path) -> BuildSpec:
    """8 rep + 4 bin (no codec) sections at distinct offsets — all
    parallel-eligible kinds, suitable for race-stress testing."""
    sections: list[Section] = []
    for i in range(8):
        (tmp_path / f"rep{i}.bin").write_bytes(bytes([i + 1] * 64))
        sections.append(Section(
            kind=SectionKind.REP, offset=0x1000 + i * 0x100,
            files=[PurePosixPath(f"rep{i}.bin")],
        ))
    for i in range(4):
        (tmp_path / f"bin{i}.bin").write_bytes(bytes([0x80 + i] * 32))
        sections.append(Section(
            kind=SectionKind.BIN, offset=0x4000 + i * 0x80,
            files=[PurePosixPath(f"bin{i}.bin")],
        ))
    return BuildSpec(sections=sections)


# ---- determinism: serial vs parallel produce identical output -------------


def test_parallel_output_matches_serial(tmp_path):
    rom_path = _make_lorom(tmp_path)
    spec = _many_section_spec(tmp_path)

    out_serial = tmp_path / "serial.sfc"
    build(spec, source_root=tmp_path, out_path=out_serial,
          original_rom=rom_path, parallel=1)

    out_parallel = tmp_path / "parallel.sfc"
    build(spec, source_root=tmp_path, out_path=out_parallel,
          original_rom=rom_path, parallel=8)

    assert out_serial.read_bytes() == out_parallel.read_bytes()


def test_parallel_repeats_are_stable(tmp_path):
    """Running parallel build many times yields byte-identical output —
    no hidden non-determinism from worker completion order."""
    rom_path = _make_lorom(tmp_path)
    spec = _many_section_spec(tmp_path)

    outs = []
    for i in range(5):
        out = tmp_path / f"run{i}.sfc"
        build(spec, source_root=tmp_path, out_path=out,
              original_rom=rom_path, parallel=8)
        outs.append(out.read_bytes())
    assert all(o == outs[0] for o in outs[1:])


# ---- mixed serial + parallel ordering -------------------------------------


def test_serial_section_observes_prior_parallel_writes(tmp_path):
    """A `<script>` (serial) following a `<rep>` (parallel) must see the
    rep's writes when it runs — `_drain_through(i)` is the linchpin."""
    rom_path = _make_lorom(tmp_path)
    # Rep writes 4 bytes at 0x6000; script sits at 0x7000 with its own table.
    (tmp_path / "patch.bin").write_bytes(b"\xAA\xBB\xCC\xDD")
    tbl = tmp_path / "ascii.tbl"
    tbl.write_text("\n".join(f"{ord(c):02X}={c}" for c in "AB") + "\n",
                   encoding="utf-8")
    txt = tmp_path / "lines.txt"
    txt.write_text("A\nB\n", encoding="utf-8")
    spec = BuildSpec(sections=[
        Section(kind=SectionKind.REP, offset=0x6000,
                files=[PurePosixPath("patch.bin")]),
        Section(kind=SectionKind.SCRIPT, offset=0x7000,
                files=[PurePosixPath("lines.txt")],
                table=PurePosixPath("ascii.tbl"),
                placement={"mode": "relocate"}),
    ])
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out,
          original_rom=rom_path, parallel=4)
    body = out.read_bytes()
    assert body[0x6000:0x6004] == b"\xAA\xBB\xCC\xDD"
    assert body[0x7000:0x7004] == b"A\x00B\x00"


# ---- reporter event protocol ----------------------------------------------


@dataclass
class _RecorderReporter(Reporter):
    events: list[tuple] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def build_started(self, total_sections):
        with self._lock:
            self.events.append(("started", total_sections))

    def section_queued(self, idx, label, kind):
        with self._lock:
            self.events.append(("queued", idx, label, kind))

    def section_status(self, idx, status, *, note="", bytes_written=0):
        with self._lock:
            self.events.append(("status", idx, status.value, bytes_written))

    def build_done(self, ok, summary):
        with self._lock:
            self.events.append(("done", ok))


def test_reporter_event_sequence(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "a.bin").write_bytes(b"\x11" * 16)
    (tmp_path / "b.bin").write_bytes(b"\x22" * 16)
    spec = BuildSpec(sections=[
        Section(kind=SectionKind.REP, offset=0x100, files=[PurePosixPath("a.bin")]),
        Section(kind=SectionKind.REP, offset=0x200, files=[PurePosixPath("b.bin")]),
    ])
    rep = _RecorderReporter()
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out,
          original_rom=rom_path, reporter=rep, parallel=2)

    kinds = [e[0] for e in rep.events]
    assert kinds[0] == "started"
    assert kinds[-1] == "done"
    # Both sections registered.
    assert ("queued", 0, "a.bin", "rep") in rep.events
    assert ("queued", 1, "b.bin", "rep") in rep.events
    # Each section reaches DONE with the right byte count.
    dones = [e for e in rep.events if e[0] == "status" and e[2] == "done"]
    by_idx = {e[1]: e[3] for e in dones}
    assert by_idx == {0: 16, 1: 16}


def test_reporter_skipped_event_for_filtered_sections(tmp_path):
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "a.bin").write_bytes(b"\x11" * 4)
    (tmp_path / "b.bin").write_bytes(b"\x22" * 4)
    spec = BuildSpec(sections=[
        Section(kind=SectionKind.REP, offset=0x100, files=[PurePosixPath("a.bin")]),
        Section(kind=SectionKind.BIN, offset=0x200, files=[PurePosixPath("b.bin")]),
    ])
    rep = _RecorderReporter()
    build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
          original_rom=rom_path, only={"bin"}, reporter=rep, parallel=2)
    statuses = [(e[1], e[2]) for e in rep.events if e[0] == "status"]
    assert (0, "skipped") in statuses
    assert any(e == (1, "done") for e in statuses)


# ---- worker-thread error propagation --------------------------------------


def test_fixed_records_parallel_matches_serial(tmp_path):
    """fixed-records is parallel-eligible — verify byte-identical output."""
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "a.bin").write_bytes(bytes(range(32)))      # 4×8
    (tmp_path / "b.bin").write_bytes(bytes(range(32, 64)))  # 4×8
    spec = BuildSpec(sections=[
        Section(kind=SectionKind.FIXED_RECORDS, offset=0x2000,
                files=[PurePosixPath("a.bin")], stride=8, count=4),
        Section(kind=SectionKind.FIXED_RECORDS, offset=0x3000,
                files=[PurePosixPath("b.bin")], stride=8, count=4),
    ])
    out_serial = tmp_path / "ser.sfc"
    out_parallel = tmp_path / "par.sfc"
    build(spec, source_root=tmp_path, out_path=out_serial,
          original_rom=rom_path, parallel=1)
    build(spec, source_root=tmp_path, out_path=out_parallel,
          original_rom=rom_path, parallel=4)
    assert out_serial.read_bytes() == out_parallel.read_bytes()
    # Sanity: both wrote the expected stride-packed bytes.
    body = out_parallel.read_bytes()
    assert body[0x2000:0x2020] == bytes(range(32))
    assert body[0x3000:0x3020] == bytes(range(32, 64))


def test_asar_with_cache_parallel_matches_serial(tmp_path, monkeypatch):
    """asar opted in via cache='1' is parallel-eligible (diff-mode write-set)
    even though the underlying handler does `rom[:] = new_rom` — workers
    operate on private scratches, then the diff ranges apply to the real rom."""
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "p1.asm").write_text("noop")
    (tmp_path / "p2.asm").write_text("noop")

    class _R:
        ok = True
        log = ""

    def _fake_apply(rom_in, patch, rom_out):
        # Patch identity by .asm filename so we can tell sections apart.
        body = bytearray(rom_in.read_bytes())
        if patch.asm_file.name == "p1.asm":
            body[0x500] = 0xAA
            body[0x501] = 0xBB
        else:
            body[0x600] = 0xCC
            body[0x601] = 0xDD
        rom_out.write_bytes(bytes(body))
        return _R()

    monkeypatch.setattr("retrotool.asm.patcher.apply_patch", _fake_apply)

    s1 = Section(kind=SectionKind.ASAR, files=[PurePosixPath("p1.asm")])
    s1.cache = True
    s2 = Section(kind=SectionKind.ASAR, files=[PurePosixPath("p2.asm")])
    s2.cache = True
    spec = BuildSpec(sections=[s1, s2])

    out_serial = tmp_path / "ser.sfc"
    out_parallel = tmp_path / "par.sfc"
    build(spec, source_root=tmp_path, out_path=out_serial,
          original_rom=rom_path, parallel=1)
    build(spec, source_root=tmp_path, out_path=out_parallel,
          original_rom=rom_path, parallel=4)
    assert out_serial.read_bytes() == out_parallel.read_bytes()
    body = out_parallel.read_bytes()
    assert body[0x500:0x502] == b"\xAA\xBB"
    assert body[0x600:0x602] == b"\xCC\xDD"


def test_asar_without_cache_stays_serial(tmp_path, monkeypatch):
    """Default-cache asar must NOT run in parallel: its return value is a
    whole-rom WriteRange that, in parallel, would clobber prior writes.
    Verify by checking that a parallel-eligible rep BEFORE a default-cache
    asar still survives in the output."""
    rom_path = _make_lorom(tmp_path)
    (tmp_path / "patch.bin").write_bytes(b"\xEE\xEE\xEE\xEE")
    (tmp_path / "p.asm").write_text("noop")

    class _R:
        ok = True
        log = ""

    def _fake_apply(rom_in, patch, rom_out):
        body = bytearray(rom_in.read_bytes())
        body[0x800] = 0xAB
        rom_out.write_bytes(bytes(body))
        return _R()

    monkeypatch.setattr("retrotool.asm.patcher.apply_patch", _fake_apply)

    spec = BuildSpec(sections=[
        Section(kind=SectionKind.REP, offset=0x400,
                files=[PurePosixPath("patch.bin")]),
        # No `s.cache = True` — default-cache asar is serial.
        Section(kind=SectionKind.ASAR, files=[PurePosixPath("p.asm")]),
    ])
    out = tmp_path / "out.sfc"
    build(spec, source_root=tmp_path, out_path=out,
          original_rom=rom_path, parallel=4)
    body = out.read_bytes()
    # rep survived (asar didn't clobber it because asar saw the rep write
    # in `before` via `_drain_through(i)` before its handler ran).
    assert body[0x400:0x404] == b"\xEE\xEE\xEE\xEE"
    assert body[0x800] == 0xAB


def test_handler_error_in_worker_propagates(tmp_path):
    rom_path = _make_lorom(tmp_path)
    # rep at offset > rom size with no grow → worker raises HandlerError
    (tmp_path / "huge.bin").write_bytes(b"\xFF" * 8)
    spec = BuildSpec(sections=[
        Section(kind=SectionKind.REP, offset=_ROM_SIZE + 0x1000,
                files=[PurePosixPath("huge.bin")]),
    ])
    rep = _RecorderReporter()
    with pytest.raises(Exception, match="extend ROM"):
        build(spec, source_root=tmp_path, out_path=tmp_path / "out.sfc",
              original_rom=rom_path, reporter=rep, parallel=4)
    # Reporter must have seen the error — even though it came from a worker.
    statuses = [(e[1], e[2]) for e in rep.events if e[0] == "status"]
    assert (0, "error") in statuses

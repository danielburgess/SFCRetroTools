"""Unit tests for Mesen2 SRAM sync helper."""
from __future__ import annotations

import tarfile
import time
from pathlib import Path

import pytest

from retrotool.debugger.mesen_saves import (
    SramSyncError,
    SramSyncResult,
    default_saves_dir,
    resolve_saves_dir,
    sync_sram,
)


def test_default_saves_dir_linux_layout():
    got = default_saves_dir()
    assert got.parts[-3:] == (".config", "Mesen2", "Saves")


def test_resolve_saves_dir_expands_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    got = resolve_saves_dir("~/myconf/Saves")
    assert got == tmp_path / "myconf" / "Saves"


def test_resolve_saves_dir_default_when_empty():
    assert resolve_saves_dir(None) == default_saves_dir()
    assert resolve_saves_dir("") == default_saves_dir()


def test_sync_copies_source_to_dest(tmp_path):
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "base.srm").write_bytes(b"save-data")

    src = tmp_path / "base.sfc"
    dst = tmp_path / "out.sfc"
    src.write_bytes(b"rom")
    dst.write_bytes(b"rom")

    result = sync_sram(src, dst, saves_dir=saves)
    assert isinstance(result, SramSyncResult)
    assert result.copied == saves / "out.srm"
    assert result.archived is None  # dest did not pre-exist
    assert (saves / "out.srm").read_bytes() == b"save-data"
    # Source SRM untouched.
    assert (saves / "base.srm").read_bytes() == b"save-data"


def test_sync_silent_when_source_srm_missing(tmp_path):
    saves = tmp_path / "Saves"
    saves.mkdir()
    src = tmp_path / "base.sfc"
    dst = tmp_path / "out.sfc"
    result = sync_sram(src, dst, saves_dir=saves)
    assert result.copied is None and result.archived is None
    assert not (saves / "out.srm").exists()


def test_sync_refuses_to_clobber_source(tmp_path):
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "base.srm").write_bytes(b"save-data")
    src = tmp_path / "base.sfc"
    dst = tmp_path / "sub" / "base.sfc"  # same stem as src — would resolve to same SRM
    with pytest.raises(SramSyncError, match="stem"):
        sync_sram(src, dst, saves_dir=saves)
    # Source SRM untouched.
    assert (saves / "base.srm").read_bytes() == b"save-data"


def test_sync_overwrites_existing_dest(tmp_path):
    """User wants iterative testing: each build starts with base SRM state."""
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "base.srm").write_bytes(b"fresh-base")
    (saves / "out.srm").write_bytes(b"stale-playtest")
    time.sleep(0.01)  # ensure copy2 mtime propagates past existing file

    src = tmp_path / "base.sfc"
    dst = tmp_path / "out.sfc"
    sync_sram(src, dst, saves_dir=saves)
    assert (saves / "out.srm").read_bytes() == b"fresh-base"


def test_sync_creates_saves_dir_if_missing(tmp_path):
    # When the user names a saves-dir that doesn't exist yet, the helper
    # should still succeed for the no-source case (silent return).
    saves = tmp_path / "nope"
    src = tmp_path / "base.sfc"
    dst = tmp_path / "out.sfc"
    result = sync_sram(src, dst, saves_dir=saves)
    assert result.copied is None


# ---- archive-before-overwrite ----------------------------------------------


def _archive_members(archive_path: Path) -> dict[str, bytes]:
    with tarfile.open(archive_path, "r:gz") as tf:
        out = {}
        for m in tf.getmembers():
            if m.isfile():
                buf = tf.extractfile(m)
                out[m.name] = buf.read() if buf else b""
        return out


def test_sync_archives_existing_dest_before_clobber(tmp_path):
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "base.srm").write_bytes(b"fresh-base")
    (saves / "out.srm").write_bytes(b"playtest-progress")

    src = tmp_path / "base.sfc"
    dst = tmp_path / "out.sfc"
    result = sync_sram(src, dst, saves_dir=saves)

    # Dest overwritten with source.
    assert (saves / "out.srm").read_bytes() == b"fresh-base"
    # Single archive per ROM, named <stem>_archive.tar.gz (no timestamp).
    assert result.archived == saves / "out_archive.tar.gz"
    assert result.archived.exists()
    members = _archive_members(result.archived)
    assert len(members) == 1
    name, data = next(iter(members.items()))
    # Entry laid out as <YYYY-MM-DD>/<stem>_<HHMMSS>.srm.
    assert name.endswith(".srm")
    assert name.startswith(time.strftime("%Y-%m-%d") + "/out_")
    assert data == b"playtest-progress"


def test_sync_accumulates_entries_across_runs(tmp_path):
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "base.srm").write_bytes(b"fresh-base")

    # First playtest + rebuild cycle.
    (saves / "out.srm").write_bytes(b"playtest-A")
    sync_sram(tmp_path / "base.sfc", tmp_path / "out.sfc", saves_dir=saves)

    # Second cycle — different playtest state.
    (saves / "out.srm").write_bytes(b"playtest-B")
    sync_sram(tmp_path / "base.sfc", tmp_path / "out.sfc", saves_dir=saves)

    archive = saves / "out_archive.tar.gz"
    members = _archive_members(archive)
    assert len(members) == 2
    payloads = set(members.values())
    assert payloads == {b"playtest-A", b"playtest-B"}


def test_sync_skips_archive_when_bytes_already_in_archive(tmp_path):
    """Same save state should not be archived twice."""
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "base.srm").write_bytes(b"fresh-base")

    (saves / "out.srm").write_bytes(b"playtest-X")
    r1 = sync_sram(tmp_path / "base.sfc", tmp_path / "out.sfc", saves_dir=saves)
    assert r1.archived is not None

    # User replays to the exact same state, rebuilds — archive should NOT grow.
    (saves / "out.srm").write_bytes(b"playtest-X")
    r2 = sync_sram(tmp_path / "base.sfc", tmp_path / "out.sfc", saves_dir=saves)
    assert r2.archived is None

    members = _archive_members(saves / "out_archive.tar.gz")
    assert len(members) == 1


def test_sync_skips_archive_when_dest_matches_source(tmp_path):
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "base.srm").write_bytes(b"same-bytes")
    (saves / "out.srm").write_bytes(b"same-bytes")

    result = sync_sram(
        tmp_path / "base.sfc", tmp_path / "out.sfc", saves_dir=saves,
    )
    assert result.archived is None
    # No archive materialized.
    assert list(saves.glob("*_archive.tar.gz")) == []


def test_sync_archive_disabled_still_overwrites(tmp_path):
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "base.srm").write_bytes(b"fresh-base")
    (saves / "out.srm").write_bytes(b"about-to-die")

    result = sync_sram(
        tmp_path / "base.sfc", tmp_path / "out.sfc",
        saves_dir=saves, archive=False,
    )
    assert result.archived is None
    assert (saves / "out.srm").read_bytes() == b"fresh-base"
    assert list(saves.glob("*_archive.tar.gz")) == []


def test_sync_archive_same_second_gets_numeric_suffix(tmp_path, monkeypatch):
    """Two distinct saves recorded within the same wall-clock second must
    land as distinct entries inside the single archive."""
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "base.srm").write_bytes(b"b1")

    frozen = 1713528000.0
    monkeypatch.setattr(time, "localtime", lambda *_: time.gmtime(frozen))

    (saves / "out.srm").write_bytes(b"first-playtest")
    sync_sram(tmp_path / "base.sfc", tmp_path / "out.sfc", saves_dir=saves)

    (saves / "out.srm").write_bytes(b"second-playtest")
    sync_sram(tmp_path / "base.sfc", tmp_path / "out.sfc", saves_dir=saves)

    members = _archive_members(saves / "out_archive.tar.gz")
    assert len(members) == 2
    names = sorted(members.keys())
    # Second entry got numeric suffix because of same-second collision.
    assert any("_1.srm" in n for n in names)

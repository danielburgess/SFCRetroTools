"""Mesen2 SRAM save-file helpers.

Mesen2 persists battery-backed RAM to `<SavesDir>/<rom_stem>.srm`. When
building a patched ROM iteratively, cloning the source ROM's SRM to the
output ROM's SRM lets the emulator start the new build at the same save
state — no manual copy step, no fresh-game reset for every rebuild.

When `archive=True`, an existing destination SRM that differs from the
source is packed into a timestamped `.tar.gz` beside the live SRM before
being overwritten — iterative testing stays safe from accidental loss of
playtest state.

Defaults target the Linux user config path. Windows/macOS paths can be
passed explicitly via `saves_dir`.
"""
from __future__ import annotations

import io
import shutil
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_DEFAULT_LINUX = Path.home() / ".config" / "Mesen2" / "Saves"


class SramSyncError(RuntimeError):
    """Raised when a requested SRAM copy would overwrite the source file."""


@dataclass
class SramSyncResult:
    """Outcome of a `sync_sram` call.

    - `copied` — destination SRM path, or None if source was absent.
    - `archived` — path of the tar.gz snapshot taken before overwrite,
      or None if no archive was created (dest missing, identical to
      source, or archiving disabled).
    """
    copied: Optional[Path] = None
    archived: Optional[Path] = None


def default_saves_dir() -> Path:
    """Return the default Mesen2 Saves directory (Linux layout)."""
    return _DEFAULT_LINUX


def resolve_saves_dir(override: Optional[str]) -> Path:
    """Expand ``~`` / return default when override is falsy."""
    if not override:
        return default_saves_dir()
    return Path(override).expanduser()


def _archive_srm(srm_path: Path, *, now: Optional[float] = None) -> Optional[Path]:
    """Append `srm_path` to `<stem>_archive.tar.gz` beside it.

    Single archive per ROM; entries laid out as
    `<YYYY-MM-DD>/<stem>_<HHMMSS>.srm`. Existing archive entries are
    preserved on rewrite (tar.gz is not append-friendly, so we round-trip
    through memory).

    Returns the archive path when a new entry was added, or `None` when
    `srm_path`'s bytes already appear in the archive — the same save
    state need not be archived twice.
    """
    lt = time.localtime(now)
    date_dir = time.strftime("%Y-%m-%d", lt)
    ts = time.strftime("%H%M%S", lt)
    archive = srm_path.parent / f"{srm_path.stem}_archive.tar.gz"
    new_bytes = srm_path.read_bytes()

    # Read existing entries into memory (info + payload). Skip entries
    # that byte-match the incoming SRM — already archived, no add needed.
    existing: list[tuple[tarfile.TarInfo, bytes]] = []
    if archive.exists():
        with tarfile.open(archive, "r:gz") as tf:
            for m in tf.getmembers():
                if not m.isfile():
                    continue
                buf = tf.extractfile(m)
                data = buf.read() if buf is not None else b""
                if data == new_bytes:
                    return None
                existing.append((m, data))

    # Resolve the final entry name, avoiding collision with same-second
    # rebuilds already in the archive.
    existing_names = {info.name for info, _ in existing}
    arcname = f"{date_dir}/{srm_path.stem}_{ts}.srm"
    if arcname in existing_names:
        i = 1
        while f"{date_dir}/{srm_path.stem}_{ts}_{i}.srm" in existing_names:
            i += 1
        arcname = f"{date_dir}/{srm_path.stem}_{ts}_{i}.srm"

    # Rewrite archive: existing entries preserved, new entry appended.
    with tarfile.open(archive, "w:gz") as tf:
        for info, data in existing:
            new_info = tarfile.TarInfo(name=info.name)
            new_info.size = len(data)
            new_info.mtime = info.mtime
            new_info.mode = info.mode
            new_info.uid = info.uid
            new_info.gid = info.gid
            tf.addfile(new_info, io.BytesIO(data))
        entry_info = tarfile.TarInfo(name=arcname)
        entry_info.size = len(new_bytes)
        entry_info.mtime = int(srm_path.stat().st_mtime)
        entry_info.mode = 0o644
        tf.addfile(entry_info, io.BytesIO(new_bytes))
    return archive


def sync_sram(
    source_rom: Path,
    out_rom: Path,
    *,
    saves_dir: Optional[Path] = None,
    archive: bool = True,
) -> SramSyncResult:
    """Copy `<saves_dir>/<source_rom.stem>.srm` → `<saves_dir>/<out_rom.stem>.srm`.

    If `archive=True` and the destination SRM already exists with content
    different from the source, the existing destination is packed into a
    timestamped tar.gz in the same directory before being overwritten.

    Returns a `SramSyncResult` with `copied` / `archived` paths populated
    as applicable. `copied` is None when the source SRM does not exist
    (silent no-op). Raises `SramSyncError` if the destination path would
    resolve to the same file as the source (e.g. same ROM stem) — the
    source SRM must never be clobbered.
    """
    saves = saves_dir if saves_dir is not None else default_saves_dir()
    src = saves / f"{source_rom.stem}.srm"
    dst = saves / f"{out_rom.stem}.srm"
    if src.resolve(strict=False) == dst.resolve(strict=False):
        raise SramSyncError(
            f"refusing to sync SRM onto itself: source and output ROM share "
            f"stem {source_rom.stem!r} (saves dir {saves})"
        )
    if not src.exists():
        return SramSyncResult()
    saves.mkdir(parents=True, exist_ok=True)

    archived: Optional[Path] = None
    if archive and dst.exists():
        # Skip archive when dest is already byte-identical to source — the
        # copy would be a no-op and an archive of the same bytes is noise.
        if dst.read_bytes() != src.read_bytes():
            archived = _archive_srm(dst)

    shutil.copy2(src, dst)
    return SramSyncResult(copied=dst, archived=archived)

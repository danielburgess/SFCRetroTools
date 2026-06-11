"""Shared handler infrastructure: context/result types, path/ROM-write
helpers, attr parsing, pointer encoders. No game- or kind-specific logic
— every sibling module imports from here, never the reverse."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from retrotool.build.spec import Section
from retrotool.core.address import SFCAddressType


@dataclass
class BuildContext:
    """State shared across section handlers during a single build.

    - `allocator` — freespace bump-allocator fed from `[build].freespace`.
    - `labels` — global label registry: name → PC offset. Populated from
      `[[build.labels]]` at parse time and from sections that declare
      `export-label=`. Script fixups of the form `[HHHH@@name]` resolve here.
    """
    allocator: Optional[object] = None  # FreespaceAllocator — loose typed to avoid import cycle
    labels: dict[str, int] = field(default_factory=dict)


class HandlerError(RuntimeError):
    """Build-time failure inside an element handler."""


@dataclass
class _PreparedScript:
    """Worker-encoded script payload — produced by `script_prepare()` against
    a rom snapshot, consumed by `handle_script()` / `_handle_script_windowed()`
    in the serial apply phase. Captures the pure encode-phase outputs (file
    I/O + table-driven encoding + sentinel detection) so the apply phase only
    does placement, allocator allocation, and label fixup resolution.

    `mode` selects which set of fields are populated:
      * `"relocate"` — `entries` from encode_script_file plus optional
        `source_snapshot` for slot-measure="source-entry".
      * `"overflow"` — `auto_entries` (encode_script_file output for the
        auto-window path), `windowed` (encode_windowed_script_file output for
        marker entries, only populated when the file actually contains
        `<<<window>>>` markers), `orig_pcs`, `ctrl_lengths`, `ctrl_table`
        (multi-prefix snapshot for downstream ctrl-aware walks), `terminator`
        (entry terminator byte), and `source_snapshot` (always captured
        pre-write for window resolution).
    """
    mode: str  # "relocate" or "overflow"
    # Relocate-mode payload:
    entries: Optional[list] = None
    source_snapshot: Optional[bytes] = None
    # Overflow-mode payload:
    auto_entries: Optional[list] = None
    windowed: Optional[list] = None
    orig_pcs: Optional[list] = None
    ctrl_lengths: Optional[dict] = None
    ctrl_table: Optional[dict] = None
    terminator: Optional[int] = None
    has_window_markers: bool = False


@dataclass
class WriteRange:
    offset: int
    length: int

    @property
    def end(self) -> int:
        return self.offset + self.length


def _resolve(file: Path, root: Path) -> Path:
    p = Path(file)
    if p.is_absolute():
        return p
    return (root / p).resolve()


def _resolve_pointer_table_pc(pointer_table, addr_type: int, *,
                              rom_len: Optional[int] = None,
                              table_len: int = 0,
                              source: str = ""):
    """Resolve a spec ``pointer_table`` to a ``(pc_offset, SFCAddress)`` pair.

    ``pointer_table`` is always a **PC file offset** (it comes from DataDef
    ``pointers.offset`` and is the same value used as the PC write target via
    ``section.offset``). It may be authored in hex (``"$13100"``) but is *not* a
    SNES address — interpreting it as a SNES address of ``addr_type`` is wrong
    (e.g. PC ``0x1BCA4`` read as ``$01:BCA4`` resolves to a different PC). The
    returned :class:`SFCAddress` view is positioned at that PC offset so callers
    can derive the table's bank byte in the ROM's mapping via ``addr_type``.

    This is the SINGLE point of truth for that convention — build handlers
    AND the extract pipeline both resolve through here, so the two sides can
    never disagree about where a pointer table lives (they used to: extract
    tried SNES-first, which silently coincides in HiROM but reads the wrong
    PC in LoROM).

    With ``rom_len`` given, validates that the table (``table_len`` bytes)
    fits inside the ROM. A value that only makes sense as a SNES address is
    diagnosed explicitly — the error names the PC offset to author instead —
    rather than crashing later or extracting from the wrong place.
    """
    from retrotool.core.address import SFCAddress, SFCAddressType

    pc = int(pointer_table)
    ptr_addr = SFCAddress(pc, SFCAddressType.PC)
    if rom_len is not None and pc + table_len > rom_len:
        alt = SFCAddress(pc, addr_type).get_address(SFCAddressType.PC)
        if alt is not None and alt + table_len <= rom_len:
            raise HandlerError(
                f"{source}: pointer table offset ${pc:06X} is outside the ROM "
                f"(size ${rom_len:06X}) when read as a PC file offset, but "
                f"resolves as a SNES address to PC ${alt:06X} under this "
                f"mapping. pointers.offset / pointer-table= takes a PC file "
                f"offset — author ${alt:06X} instead."
            )
        raise HandlerError(
            f"{source}: pointer table read of {table_len}b at PC ${pc:06X} "
            f"exceeds ROM size ${rom_len:06X}"
        )
    return pc, ptr_addr


def _read_concat(section: Section, root: Path) -> bytes:
    chunks: list[bytes] = []
    for f in section.files:
        path = _resolve(Path(str(f)), root)
        if not path.exists():
            raise HandlerError(f"{section.source}: file not found: {path}")
        chunks.append(path.read_bytes())
    return b"".join(chunks)


def _write(rom: bytearray, offset: int, data: bytes, *, allow_grow: bool, source: str) -> WriteRange:
    end = offset + len(data)
    if end > len(rom):
        if not allow_grow:
            raise HandlerError(
                f"{source}: write {len(data)} bytes at {offset:#x} would extend "
                f"ROM past {len(rom):#x} (use grow='insert' to allow growth)"
            )
        rom.extend(b"\x00" * (end - len(rom)))
    rom[offset:end] = data
    return WriteRange(offset=offset, length=len(data))


# ---- handlers -------------------------------------------------------------

def _attr_hex(v: Optional[str]) -> Optional[int]:
    """Parse a graphics-section numeric attr. `$`/`0x` prefix → hex; bare → dec.
    `$BB:AAAA` colons are stripped (matches project.toml offset convention)."""
    if v is None:
        return None
    s = str(v).strip().replace("_", "")
    if not s:
        return None
    if s.startswith("$"):
        return int(s.replace(":", "")[1:], 16)
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(s, 10)


def _load_callable(ref: str, root: Path, source: str = "") -> Callable:
    """Resolve a `path/to/mod.py:func` or `pkg.mod:func` reference to a callable.
    File-path refs are resolved relative to the project root and imported by
    location; dotted refs go through normal `import`."""
    import importlib
    import importlib.util
    if ":" not in ref:
        raise HandlerError(f"{source}: expected 'module:function', got {ref!r}")
    mod_ref, func = ref.rsplit(":", 1)
    if mod_ref.endswith(".py") or "/" in mod_ref or "\\" in mod_ref:
        path = _resolve(Path(mod_ref), root)
        if not path.exists():
            raise HandlerError(f"{source}: module file not found: {path}")
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise HandlerError(
                f"{source}: cannot import {path} as a Python module")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    else:
        mod = importlib.import_module(mod_ref)
    fn = getattr(mod, func, None)
    if not callable(fn):
        raise HandlerError(f"{source}: {mod_ref} has no callable {func!r}")
    return fn


def _run_callable(fn: Callable, rom: bytearray, section: Section, root: Path,
                  ctx: Optional[BuildContext]):
    """Call a project build callable and normalize its result to WriteRange(s).
    The callable mutates `rom` directly and returns WriteRange | list | None."""
    result = fn(rom, section, root, ctx)
    if result is None:
        return WriteRange(offset=section.offset or 0, length=0)
    if isinstance(result, WriteRange):
        return result
    if isinstance(result, (list, tuple)):
        out = list(result)
        if all(isinstance(r, WriteRange) for r in out):
            return out
    raise HandlerError(
        f"{section.source}: build callable must return WriteRange | "
        f"list[WriteRange] | None, got {type(result).__name__}")


def _diff_ranges(before: bytes, after: bytes) -> list[WriteRange]:
    """Contiguous runs of changed bytes between `before` and `after`.

    Bytes present only in `after` (tail extension) count as changed.
    Shrinkage isn't representable as a WriteRange set — caller validates.
    """
    if len(after) < len(before):
        raise ValueError("after must be at least as long as before")
    ranges: list[WriteRange] = []
    n = len(before)
    m = len(after)
    i = 0
    while i < m:
        if i < n and before[i] == after[i]:
            i += 1
            continue
        start = i
        while i < m and (i >= n or before[i] != after[i]):
            i += 1
        ranges.append(WriteRange(offset=start, length=i - start))
    return ranges


def _parse_pipe_kvs(label: str, raw: str, source: str) -> dict[str, str]:
    """Shared parser for `defines=K=V|K=V` / `constants=K=V|K=V` attrs.

    Handlers (asar, bass) accept the same `|`-separated, `=`-delimited
    key/value list. Splitting the parser out keeps the two handlers
    structurally identical — adding or fixing a parsing edge case lands
    in one place.
    """
    out: dict[str, str] = {}
    for kv in (raw or "").split("|"):
        if not kv:
            continue
        if "=" not in kv:
            raise HandlerError(f"{source}: {label} {kv!r} missing '='")
        k, v = kv.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _wrap_assembler_writes(
    *, rom: bytearray, before: bytes, new_rom: bytes,
    section: Section, label: str,
):
    """Common post-process for asar / bass handlers.

    Validates non-shrink (overridable via `allow-shrink="1"`), commits
    `new_rom` into `rom`, and returns either a single full-ROM WriteRange
    (default — cache off) or per-byte-run diff ranges (cache opt-in).
    Mirrors the behavior `handle_asar` had as inline code; pulled out so
    `handle_bass` doesn't drift when the asar one gets tuned."""
    allow_shrink = (section.attrs.get("allow-shrink") or "").lower() in (
        "1", "true", "yes",
    )
    if len(new_rom) < len(rom) and not allow_shrink:
        raise HandlerError(
            f"{section.source}: {label} shrank ROM from {len(rom)} to "
            f"{len(new_rom)} bytes (set allow-shrink=\"1\" to permit)"
        )
    rom[:] = new_rom
    if section.cache:
        if len(new_rom) < len(before):
            # Diff representation can't encode shrinkage cleanly — fall
            # back to a whole-ROM WriteRange in that case.
            return WriteRange(offset=0, length=len(new_rom))
        return _diff_ranges(before, new_rom)
    return WriteRange(offset=0, length=len(new_rom))


def _pc_to_lorom1_bytes(pc: int) -> bytes:
    """24-bit LoROM1 SNES address, little-endian. Bank = ((pc>>15)&0x7F)|0x80."""
    bank = ((pc >> 15) & 0x7F) | 0x80
    addr = (pc & 0x7FFF) | 0x8000
    return bytes([addr & 0xFF, (addr >> 8) & 0xFF, bank & 0xFF])


def _pc_to_lorom0_bytes(pc: int) -> bytes:
    """24-bit LoROM0 SNES address, little-endian. Bank = (pc>>15)&0x7F (no $80)."""
    bank = (pc >> 15) & 0x7F
    addr = (pc & 0x7FFF) | 0x8000
    return bytes([addr & 0xFF, (addr >> 8) & 0xFF, bank & 0xFF])


def _pc_to_lorom_within_bank(pc: int) -> int:
    """16-bit within-bank LoROM address (bank implicit, $8000-$FFFF half)."""
    return (pc & 0x7FFF) | 0x8000


def _pc_to_hirom_bytes(pc: int) -> bytes:
    """24-bit HiROM SNES address, little-endian. Bank = (pc>>16) | 0xC0."""
    bank = ((pc >> 16) & 0x3F) | 0xC0
    addr = pc & 0xFFFF
    return bytes([addr & 0xFF, (addr >> 8) & 0xFF, bank & 0xFF])


def _pc_to_hirom_within_bank(pc: int) -> int:
    """16-bit within-bank HiROM address (bank implicit, full 64KB bank)."""
    return pc & 0xFFFF


def _select_24bit_encoder(addr_type: int) -> Callable[[int], bytes]:
    """Pick the PC→24-bit SNES address encoder matching the section's mapping.

    Used by relocate-mode pointer-table emission and `[HHHH@N]` fixup
    resolution when no explicit `overflow.pointer-encoder` is configured.
    """
    if addr_type == SFCAddressType.HIROM:
        return _pc_to_hirom_bytes
    if addr_type == SFCAddressType.LOROM2:
        return _pc_to_lorom0_bytes
    # LOROM1 / EXHIROM / EXLOROM / unset → default LoROM1 (original behavior).
    return _pc_to_lorom1_bytes


def _select_16bit_within_bank(addr_type: int) -> Callable[[int], int]:
    """Pick the PC→16-bit-within-bank encoder matching the section's mapping."""
    if addr_type == SFCAddressType.HIROM:
        return _pc_to_hirom_within_bank
    return _pc_to_lorom_within_bank


def _ensure_room(rom: bytearray, end: int) -> None:
    if end > len(rom):
        rom.extend(b"\x00" * (end - len(rom)))


# Dispatch table. Kinds without a Phase-2 handler raise via the default.
# Handlers may return a single WriteRange for a contiguous write, or a
# `list[WriteRange]` when a section produces multiple disjoint writes
# (e.g. the script handler emits pointer-table + per-entry inline +
# freespace tails). The build driver normalizes both shapes to a list.
#
# The optional 4th arg (`ctx: BuildContext`) is passed by the build driver.
# Handlers that don't need it keep the default `None` and the parameter
# costs nothing.
HandlerFn = Callable[
    [bytearray, Section, Path, Optional[BuildContext]],
    "WriteRange | list[WriteRange]",
]


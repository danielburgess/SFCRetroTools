"""Per-`SectionKind` build handlers.

Each handler takes a mutable bytearray (the working ROM body, SMC header stripped)
and a `Section`, and writes its bytes. Handlers return the (offset, length) range
they wrote — the caller uses this to grow the buffer if needed and to summarize
the build.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from retrotool.build.spec import Section, SectionKind


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

def handle_rep(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    if section.offset is None:
        raise HandlerError(f"{section.source}: <rep> requires offset")
    data = _read_concat(section, root)
    return _write(rom, section.offset, data, allow_grow=False, source=section.source or "")


def handle_ins(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    if section.offset is None:
        raise HandlerError(f"{section.source}: <ins> requires offset")
    data = _read_concat(section, root)
    return _write(rom, section.offset, data, allow_grow=True, source=section.source or "")


def handle_bin(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    """Unified raw/compressed-bytes handler. Honors codec= and grow= (default 'replace')."""
    if section.offset is None:
        raise HandlerError(f"{section.source}: <bin> requires offset")
    pre = getattr(section, "_prepared", None)
    if pre is not None:
        data = pre
        grow = (section.grow or "replace").lower()
        if grow not in {"replace", "insert", "fail"}:
            raise HandlerError(f"{section.source}: invalid grow={section.grow!r}")
        return _write(rom, section.offset, data,
                      allow_grow=(grow == "insert"), source=section.source or "")
    data = _read_concat(section, root)

    if section.codec:
        from retrotool.compression import registry as codec_registry
        try:
            codec = codec_registry.get(section.codec)
        except KeyError as e:
            raise HandlerError(
                f"{section.source}: unknown codec={section.codec!r}. "
                f"Known: {codec_registry.list_schemes()}"
            ) from e
        result = codec.compress(data)
        data = result.data

    if section.size is not None and section.size != len(data):
        if len(data) > section.size:
            raise HandlerError(
                f"{section.source}: <bin> data ({len(data)}b) exceeds declared size ({section.size}b)"
            )
        data = data + b"\x00" * (section.size - len(data))

    grow = (section.grow or "replace").lower()
    if grow not in {"replace", "insert", "fail"}:
        raise HandlerError(f"{section.source}: invalid grow={section.grow!r}")
    allow_grow = grow == "insert"
    return _write(rom, section.offset, data, allow_grow=allow_grow, source=section.source or "")


# Bitplane transforms. Map `encode` value → (forward, reverse) functions.
# forward = source-file bytes → ROM bytes (build direction).
# reverse = ROM bytes → source-file bytes (extract direction).
# Currently only identity (`None`, `"raw"`, `"planar"`) is wired. Named
# transformations like MBuild's "2bpp-to-1bpp-il" raise HandlerError pointing
# to the roadmap; the bitplane registry is structured so a later phase can
# drop in real conversions without touching the handler.
_IDENTITY = lambda b: b
_BITPLANE_TRANSFORMS: dict[str, tuple[Callable[[bytes], bytes], Callable[[bytes], bytes]]] = {
    "": (_IDENTITY, _IDENTITY),
    "raw": (_IDENTITY, _IDENTITY),
    "planar": (_IDENTITY, _IDENTITY),
}


def handle_graphics(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    """Tile/palette/tilemap data. Phase 3 wires raw planar passthrough; bitplane
    repacking (e.g. MBuild's "2bpp-to-1bpp-il") lands in a later phase."""
    if section.offset is None:
        raise HandlerError(f"{section.source}: <graphics> requires offset")
    pre = getattr(section, "_prepared", None)
    if pre is not None:
        grow = (section.grow or "replace").lower()
        return _write(rom, section.offset, pre,
                      allow_grow=(grow == "insert"), source=section.source or "")
    data = _read_concat(section, root)
    key = (section.codec or "").lower()
    if key not in _BITPLANE_TRANSFORMS:
        raise HandlerError(
            f"{section.source}: bitplane transform encode={section.codec!r} not yet "
            f"implemented. Known: {sorted(_BITPLANE_TRANSFORMS)}"
        )
    forward, _ = _BITPLANE_TRANSFORMS[key]
    data = forward(data)
    grow = (section.grow or "replace").lower()
    allow_grow = grow == "insert"
    return _write(rom, section.offset, data, allow_grow=allow_grow, source=section.source or "")


def bitplane_reverse(encode: Optional[str]) -> Callable[[bytes], bytes]:
    """Resolve the reverse-direction bitplane transform (used by extract)."""
    key = (encode or "").lower()
    if key not in _BITPLANE_TRANSFORMS:
        raise HandlerError(f"bitplane transform encode={encode!r} not yet implemented")
    return _BITPLANE_TRANSFORMS[key][1]


def handle_project(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    """Run a nested mbxml build against the current working ROM. The sub-spec's
    own `original` attr is ignored — the parent ROM is the canvas. Sub-spec
    sections are dispatched in order, with the sub-spec's own vars in scope
    (parent vars are not inherited automatically; use `<include>` for that).
    """
    src_attr = section.attrs.get("src") or (section.files[0] if section.files else None)
    if src_attr is None:
        raise HandlerError(f"{section.source}: <project> requires src=…")

    src_path = _resolve(Path(str(src_attr)), root)
    if not src_path.exists():
        raise HandlerError(f"{section.source}: project src not found: {src_path}")

    # Local import — front-end → handler is a one-direction dep elsewhere; this
    # is the only place handlers reach back into a front-end loader.
    from retrotool.build.front_ends.mbxml import parse_mbxml

    sub_spec = parse_mbxml(src_path)
    sub_root = src_path.parent
    if sub_spec.path is not None:
        sub_root = (sub_root / Path(str(sub_spec.path))).resolve()

    for sub in sub_spec.sections:
        if sub.condition is not None:
            from retrotool.build.interpolate import evaluate_condition
            if not evaluate_condition(sub.condition, sub_spec.vars, source=sub.source or ""):
                continue
        h = HANDLERS.get(sub.kind)
        if h is None:
            raise HandlerError(
                f"{sub.source}: <project> sub-section kind <{sub.kind.value}> "
                f"has no handler"
            )
        h(rom, sub, sub_root, ctx)

    # <project> is non-cacheable; return a zero-length sentinel so the caller
    # doesn't treat the whole ROM as this section's output.
    return WriteRange(offset=0, length=0)


def handle_asar(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    """Apply an asar patch to the working ROM. Round-trips through a temp file
    because the `asar` CLI operates on disk. Section.attrs format:
      file=patch.asm  (required)
      includes=A|B|C  (optional, |-separated additional include dirs)
      defines=K=V|K=V (optional, |-separated define list)
    """
    if not section.files:
        raise HandlerError(f"{section.source}: <asar> requires file=… (.asm)")

    import tempfile
    from retrotool.asm.patcher import AsarPatch, apply_patch

    asm_file = _resolve(Path(str(section.files[0])), root)
    if not asm_file.exists():
        raise HandlerError(f"{section.source}: asar patch not found: {asm_file}")

    raw = section.attrs
    includes = [
        _resolve(Path(p), root) for p in (raw.get("includes") or "").split("|") if p
    ]
    defines: dict[str, str] = {}
    for kv in (raw.get("defines") or "").split("|"):
        if not kv:
            continue
        if "=" not in kv:
            raise HandlerError(f"{section.source}: asar define {kv!r} missing '='")
        k, v = kv.split("=", 1)
        defines[k.strip()] = v.strip()

    with tempfile.TemporaryDirectory(prefix="retrotool-asar-") as td:
        tdp = Path(td)
        rom_in = tdp / "in.sfc"
        rom_out = tdp / "out.sfc"
        rom_in.write_bytes(bytes(rom))
        result = apply_patch(rom_in, AsarPatch(asm_file, includes, defines), rom_out)
        if not result.ok:
            raise HandlerError(f"{section.source}: asar failed:\n{result.log}")
        new_rom = rom_out.read_bytes()

    # Asar may grow the ROM (BANK directives) but must not shrink it — a shorter
    # result silently truncates downstream section writes. Opt out per-section
    # with allow-shrink="1" when the shrink is intentional.
    allow_shrink = (section.attrs.get("allow-shrink") or "").lower() in ("1", "true", "yes")
    if len(new_rom) < len(rom) and not allow_shrink:
        raise HandlerError(
            f"{section.source}: asar shrank ROM from {len(rom)} to {len(new_rom)} bytes "
            f"(set allow-shrink=\"1\" to permit)"
        )
    rom[:] = new_rom
    return WriteRange(offset=0, length=len(new_rom))


def handle_libsfx(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    """Build a libSFX project and install the resulting ROM as the working canvas.

    Attrs:
      src=…      (required) project root, relative to the .mbxml file
      debug=0|1|2
      stack-size=<int>      ca65 __STACKSIZE__ override
      out=<handle>          reserved — currently ignored (always replaces working ROM)

    Subsequent <rep>/<ins>/<bin>/<asar> sections operate on the built ROM.
    """
    src_attr = section.attrs.get("src") or (section.files[0] if section.files else None)
    if src_attr is None:
        raise HandlerError(f"{section.source}: <libsfx> requires src=…")
    proj_root = _resolve(Path(str(src_attr)), root)
    if not proj_root.exists() or not proj_root.is_dir():
        raise HandlerError(f"{section.source}: libsfx project root not found: {proj_root}")

    from retrotool.asm.libsfx import LibSFXProject

    project = LibSFXProject.discover(proj_root)
    raw = section.attrs
    if "debug" in raw:
        try:
            project.cfg.debug = int(raw["debug"])
        except ValueError as e:
            raise HandlerError(f"{section.source}: <libsfx> debug= not int: {raw['debug']!r}") from e
    if "stack-size" in raw:
        try:
            project.cfg.stack_size = int(raw["stack-size"], 0)
        except ValueError as e:
            raise HandlerError(
                f"{section.source}: <libsfx> stack-size= not int: {raw['stack-size']!r}"
            ) from e

    import tempfile
    with tempfile.TemporaryDirectory(prefix="retrotool-libsfx-") as td:
        out_rom = Path(td) / f"{project.cfg.name}.sfc"
        result = project.build(out_rom=out_rom)
        new_bytes = result.rom.read_bytes()

    from retrotool.core.rom import _strip_smc_header
    _, body = _strip_smc_header(new_bytes)
    rom[:] = body
    return WriteRange(offset=0, length=len(rom))


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


def _ensure_room(rom: bytearray, end: int) -> None:
    if end > len(rom):
        rom.extend(b"\x00" * (end - len(rom)))


def _script_placement_mode(section: Section, root: Path) -> str:
    """Return `section.placement.mode`; required to be `"overflow"` or
    `"relocate"`. No default: mis-default silently corrupts sibling tables
    sharing data regions (relocate rewrites ptr-table, overflow does not).
    """
    m = (section.placement or {}).get("mode")
    if m in ("overflow", "relocate"):
        return m
    if m is None:
        raise HandlerError(
            f"{section.source}: script section requires explicit "
            f"placement.mode ('overflow' or 'relocate'); no default"
        )
    raise HandlerError(
        f"{section.source}: placement.mode must be 'overflow' or "
        f"'relocate', got {m!r}"
    )


def handle_script(
    rom: bytearray, section: Section, root: Path,
    ctx: Optional[BuildContext] = None,
) -> list[WriteRange]:
    """Pointer-table-driven script insertion.

    Two placement modes (see `_script_placement_mode`):

    - `relocate` (default): emit pointer table at `pointer-table`, then each
      entry's encoded text at `pointer-table + count * pointer-size` onward
      (sequential, dedupe on `orig_addr`). Overflow via `section.overflow`
      lands entries in freespace through `ctx.allocator`. `[HHHH@N[:label]]`
      and `[HHHH@@name]` fixups resolve after every placement is known.
    - `overflow`: pointer table untouched; for each `<<<window[N]:$S-$E>>>`
      block in the source file, patch an FFC0 redirect at the window's
      source offset and write the encoded text + FFC0-return tail into
      `ctx.allocator` freespace. Delegates to `_handle_script_windowed`.
    """
    if section.table is None:
        raise HandlerError(f"{section.source}: <script> requires table=")
    if not section.files:
        raise HandlerError(f"{section.source}: <script> requires file=")

    if _script_placement_mode(section, root) == "overflow":
        return _handle_script_windowed(rom, section, root, ctx)
    # Legacy mode: no pointer-table, just concatenate Table.encode_text(line)
    # joined with $00. Kept so pre-phase-6 specs keep working.
    if section.pointer_table is None:
        if section.offset is None:
            raise HandlerError(
                f"{section.source}: <script> requires pointer-table= "
                f"(or offset= for legacy concat mode)"
            )
        from retrotool.script.table import Table as _LegacyTable
        tbl = _LegacyTable(_resolve(Path(str(section.table)), root))
        text = _resolve(Path(str(section.files[0])), root).read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if ln]
        data = b"\x00".join(tbl.encode_text(ln) for ln in lines) + b"\x00"
        grow = (section.grow or "replace").lower()
        return _write(rom, section.offset, data,
                      allow_grow=(grow == "insert"), source=section.source or "")

    if section.count is None:
        raise HandlerError(f"{section.source}: <script> requires count=")

    from retrotool.script.encode import encode_script_file  # deferred import

    ptr_size = section.pointer_size or 2
    if ptr_size not in (2, 3):
        raise HandlerError(
            f"{section.source}: pointer-size must be 2 or 3, got {ptr_size}"
        )

    script_path = _resolve(Path(str(section.files[0])), root)
    table_path = _resolve(Path(str(section.table)), root)
    fallback_path = (
        _resolve(Path(str(section.fallback_table)), root)
        if section.fallback_table else None
    )

    entries = encode_script_file(
        script_path, table_path,
        fallback_table=fallback_path,
        word_wrap=section.word_wrap,
        textbuf_limit=section.textbuf_limit,
        sub_table_filter=section.pointer_table,
    )
    count = int(section.count)
    while len(entries) < count:
        entries.append((b"\x00", None, [], {}))
    entries = entries[:count]

    ptr_tbl_pc = section.pointer_table
    ptr_tbl_len = count * ptr_size
    data_start = ptr_tbl_pc + ptr_tbl_len

    # Overflow strategy (optional). Built from `section.overflow` via the
    # registry — no game-specific bytes leak into handler code. Entries that
    # fit their source slot go inline unchanged; oversize entries are packed
    # by the strategy (spill to freespace, inline stub, etc.).
    from retrotool.build.overflow import (
        Entry as _OverflowEntry,
        strategy_from_config as _strategy_from_config,
        get_pointer_encoder as _get_pointer_encoder,
    )
    overflow_strategy = None
    slot_measure = "pointer-distance"
    _slot_tbl = None
    if section.overflow is not None:
        # Host-side splitter context (e.g. ctrl_lengths from the loaded
        # Table) so generic splitters like `ctrl-aware` don't need the
        # table baked into config.
        from retrotool.script.table import Table as _SplitTable
        _slot_tbl = _SplitTable(str(table_path))
        splitter_ctx = {
            "ctrl_lengths": getattr(_slot_tbl, "ctrl_lengths", {}) or {},
        }
        overflow_strategy = _strategy_from_config(
            section.overflow, splitter_ctx=splitter_ctx,
        )
        sm = section.overflow.get("slot-measure")
        if sm:
            slot_measure = str(sm)
            if slot_measure not in ("pointer-distance", "source-entry"):
                raise HandlerError(
                    f"{section.source}: overflow.slot-measure must be "
                    f"'pointer-distance' or 'source-entry', got {slot_measure!r}"
                )
    # Snapshot source ROM data region for `source-entry` slot measurement.
    # Captured before any writes so EN entries whose original JP bytes were
    # shorter than the pointer slot still overflow matching LM3 semantics.
    _source_snapshot: Optional[bytes] = None
    if slot_measure == "source-entry":
        _source_snapshot = bytes(rom)
    # Pointer-encoder applied to `[HHHH@N[:label]]` and `[HHHH@@name]` fixup
    # resolution. Defaults to SNES LoROM1 24-bit LE to match the encoder's
    # 3-byte placeholder. Overridable via `section.overflow.pointer-encoder`.
    fixup_pointer_encoder: Callable[[int], bytes] = _pc_to_lorom1_bytes
    if section.overflow is not None:
        enc_name = section.overflow.get("pointer-encoder")
        if enc_name:
            fixup_pointer_encoder = _get_pointer_encoder(str(enc_name))

    # Sentinel passthrough. Source ROMs sometimes pad the ptr table tail with
    # entries that decode outside the LoROM window (system-area mirrors, etc.).
    # Those slots hold no real text; re-encoding the script bytes in their
    # place would consume data-region space and shift every subsequent ptr.
    # Detect by decoding the source ptr under LoROM1 — if `lorom1_to_pc` is
    # None, carry the raw source bytes straight through into the output ptr
    # table and skip the data write. Snapshot before any writes since handlers
    # mutate `rom` in place.
    from retrotool.core.address import SFCAddress, SFCAddressType
    _ensure_room(rom, ptr_tbl_pc + ptr_tbl_len)
    src_ptr_bytes = bytes(rom[ptr_tbl_pc:ptr_tbl_pc + ptr_tbl_len])
    bank_hi = (
        SFCAddress(ptr_tbl_pc).get_bank_byte(SFCAddressType.LOROM1)
        if ptr_size == 2 else 0
    )
    sentinel_raw: dict[int, bytes] = {}
    src_pc: dict[int, int] = {}
    zero_slot = b"\x00" * ptr_size
    for i in range(count):
        raw = src_ptr_bytes[i * ptr_size:(i + 1) * ptr_size]
        if raw == zero_slot:
            # Fresh/blank ptr table — nothing to carry through. Fall through
            # to sequential packing.
            continue
        if ptr_size == 3:
            snes = raw[0] | (raw[1] << 8) | (raw[2] << 16)
        else:
            snes = (bank_hi << 16) | raw[0] | (raw[1] << 8)
        pc = SFCAddress.lorom1_to_pc(snes, verbose=False)
        if pc is None:
            sentinel_raw[i] = raw
        else:
            src_pc[i] = pc

    writes: list[WriteRange] = []
    ptrs: list[Optional[int]] = []
    entry_pc: dict[int, int] = {}
    entry_labels_pc: dict[int, dict[str, int]] = {}
    pending: list[tuple[int, object]] = []  # (rom_pc_of_placeholder, ScriptFixup)
    seen_addrs: dict[int, int] = {}
    # Duplicate-source-ptr dedupe. Source ROMs share one entry body across
    # multiple ptr slots (e.g. LM3 scene-desc-name ptrs 117/118/123 all point
    # at $B50E, a 12-byte body). Without dedupe, entry 118's body bumps to
    # `cur` because its source slot (next distinct src_pc after $B50E) is
    # zero-width, and the ptr table drifts. Share the first placement's PC
    # on every later hit; skip the data write.
    seen_src_pc: dict[int, int] = {}

    def _map_source_offset(
        source_offset: int, inline_pc: int, source_split: int,
        tail_pc: Optional[int],
    ) -> int:
        """Translate a byte offset within the encoded source bytes into the
        rom PC where that byte actually landed after packing. Offsets below
        `source_split` landed in the inline write; anything at or above
        landed at the start of the (first) tail write."""
        if source_offset < source_split:
            return inline_pc + source_offset
        if tail_pc is None:
            raise HandlerError(
                f"{section.source}: fixup at source offset {source_offset} "
                f"spilled to tail but no tail was allocated"
            )
        return tail_pc + (source_offset - source_split)

    # Placement: honor the source ROM's per-entry PC when known. Sequential
    # packing obliterates any unrelated data that happens to live in gaps
    # between entries (common when a text block shares a region with
    # sibling tables). Writing at each entry's source PC preserves those
    # gaps untouched, giving byte-equal round-trip for unchanged scripts.
    # If re-encoded content outgrows its source slot (next entry's PC) and
    # no overflow strategy is configured, we fall back to sequential bump
    # from `cur`. With an overflow strategy configured, oversize entries
    # are routed through the strategy (inline stub + tail in freespace).
    cur = data_start
    for i, (enc, orig_addr, ent_fixups, ent_labels) in enumerate(entries):
        if i in sentinel_raw:
            ptrs.append(None)
            continue
        # Dedupe by source ptr PC: if an earlier entry already placed at
        # this same source PC, share its output PC and skip the write.
        if i in src_pc and src_pc[i] in seen_src_pc:
            shared_pc = seen_src_pc[src_pc[i]]
            entry_pc[i] = shared_pc
            ptrs.append(shared_pc)
            continue
        is_dup = (orig_addr is not None and orig_addr in seen_addrs
                  and enc == b"\x00")
        if is_dup:
            pc = seen_addrs[orig_addr]
            ptrs.append(pc)
            continue

        pc = src_pc.get(i, cur)
        # Slot end = nearest-greater src PC across all entries (not just
        # next index). Duplicate src ptrs and non-monotonic ordering
        # break the naive next-index scan.
        slot_end: Optional[int] = None
        if i in src_pc:
            greater = [p for p in src_pc.values() if p > src_pc[i]]
            slot_end = min(greater) if greater else None
        max_inline = (slot_end - pc) if slot_end is not None else len(enc)
        if slot_measure == "source-entry" and _source_snapshot is not None and _slot_tbl is not None:
            # Walk source ROM from the source ptr's PC using ctrl-aware
            # terminator detection; treat that length (not the raw pointer
            # distance) as the max inline budget. Matches extraction
            # semantics for ROMs whose original entries are shorter than
            # the slot they occupy.
            end = _slot_tbl.find_entry_end(_source_snapshot, pc, max_addr=slot_end)
            max_inline = end - pc

        source_split = len(enc)
        first_tail_pc: Optional[int] = None
        pack_fixups: list[object] = []

        if overflow_strategy is not None and max_inline < len(enc):
            # Oversize — delegate to strategy.
            packed = overflow_strategy.pack(
                _OverflowEntry(
                    id=f"{section.source}[{i}]",
                    encoded=enc,
                    max_inline=max_inline,
                    original_offset=pc,
                ),
                ctx.allocator if ctx is not None else None,
            )
            if packed.preserve_source:
                # Strategy opted out — leave source ROM bytes untouched for
                # this entry (slot too small for a stub; cross-entry pins
                # likely target the original bytes).
                source_split = 0
                entry_pc[i] = pc
                continue
            inline_bytes = packed.inline
            source_split = packed.source_split
            _ensure_room(rom, pc + len(inline_bytes))
            rom[pc:pc + len(inline_bytes)] = inline_bytes
            writes.append(WriteRange(offset=pc, length=len(inline_bytes)))
            for tw in packed.tails:
                _ensure_room(rom, tw.offset + len(tw.data))
                rom[tw.offset:tw.offset + len(tw.data)] = tw.data
                writes.append(WriteRange(offset=tw.offset, length=len(tw.data)))
            if packed.tails:
                first_tail_pc = packed.tails[0].offset
            pack_fixups = list(packed.fixups)
        else:
            # Source slot either fits or there's no slot constraint; also,
            # without an overflow strategy we keep the old sequential-bump
            # fallback so existing no-overflow configs behave unchanged.
            if (slot_end is not None and pc + len(enc) > slot_end
                    and overflow_strategy is None):
                pc = cur  # overflow of source slot → sequential fallback
            _ensure_room(rom, pc + len(enc))
            rom[pc:pc + len(enc)] = enc
            writes.append(WriteRange(offset=pc, length=len(enc)))

        entry_pc[i] = pc
        if ent_labels:
            entry_labels_pc[i] = {
                n: _map_source_offset(off, pc, source_split, first_tail_pc)
                for n, off in ent_labels.items()
            }
        for fx in ent_fixups:
            rom_pc_of_placeholder = _map_source_offset(
                fx.offset, pc, source_split, first_tail_pc,
            )
            pending.append((rom_pc_of_placeholder, fx))
        # Resolve strategy-returned PackFixups (e.g. inline-redirect's
        # redirect-pointer slot) now — the tail PC is known.
        for pf in pack_fixups:
            _ensure_room(rom, pc + pf.inline_offset + 3)
            ptr_bytes = fixup_pointer_encoder(pf.target_pc)
            rom[pc + pf.inline_offset:pc + pf.inline_offset + len(ptr_bytes)] = ptr_bytes
        if orig_addr is not None:
            seen_addrs[orig_addr] = pc
        if i in src_pc:
            seen_src_pc.setdefault(src_pc[i], pc)
        # `cur` tracks the sequential fallback cursor; only advance past the
        # inline write (tails live in freespace, not in the data region).
        cur = max(cur, pc + source_split)
        ptrs.append(pc)

    # Resolve all entry/global fixups uniformly.
    for rom_pc, fx in pending:
        if fx.global_label is not None:
            if ctx is None or fx.global_label not in ctx.labels:
                raise HandlerError(
                    f"{section.source}: [HHHH@@{fx.global_label}] — "
                    f"unknown global label"
                )
            target_pc = ctx.labels[fx.global_label]
        else:
            if fx.entry_idx not in entry_pc:
                raise HandlerError(
                    f"{section.source}: [HHHH@{fx.entry_idx}] → missing entry"
                )
            target_pc = entry_pc[fx.entry_idx]
            if fx.label is not None:
                target_labels = entry_labels_pc.get(fx.entry_idx, {})
                if fx.label not in target_labels:
                    raise HandlerError(
                        f"{section.source}: [HHHH@{fx.entry_idx}:{fx.label}] — "
                        f"label not defined in target entry"
                    )
                target_pc = target_labels[fx.label]
        addr = fixup_pointer_encoder(target_pc)
        _ensure_room(rom, rom_pc + len(addr))
        rom[rom_pc:rom_pc + len(addr)] = addr

    # Emit pointer table.
    _ensure_room(rom, ptr_tbl_pc + ptr_tbl_len)
    for i, pc in enumerate(ptrs):
        if pc is None:
            raw = sentinel_raw[i]
            rom[ptr_tbl_pc + i * ptr_size:ptr_tbl_pc + (i + 1) * ptr_size] = raw
        elif ptr_size == 3:
            rom[ptr_tbl_pc + i * 3:ptr_tbl_pc + i * 3 + 3] = _pc_to_lorom1_bytes(pc)
        else:
            ptr16 = _pc_to_lorom_within_bank(pc)
            rom[ptr_tbl_pc + i * 2] = ptr16 & 0xFF
            rom[ptr_tbl_pc + i * 2 + 1] = (ptr16 >> 8) & 0xFF
    writes.insert(0, WriteRange(offset=ptr_tbl_pc, length=ptr_tbl_len))
    return writes


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

def handle_fixed_records(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    """Fixed-stride record table. Source = packed binary `file=`. Validates
    `len(file) == stride * count` (when both given). The structured-fields
    encoder (TOML records → packed bytes per `fields=` schema) is a follow-on."""
    if section.offset is None:
        raise HandlerError(f"{section.source}: <fixed-records> requires offset")
    if not section.files:
        raise HandlerError(f"{section.source}: <fixed-records> requires file=…")
    data = _read_concat(section, root)
    stride = section.stride
    count = section.count
    if stride is not None and count is not None:
        expected = stride * count
        if len(data) != expected:
            raise HandlerError(
                f"{section.source}: <fixed-records> file is {len(data)}b, "
                f"expected stride*count = {stride}*{count} = {expected}b"
            )
    elif stride is not None and len(data) % stride != 0:
        raise HandlerError(
            f"{section.source}: <fixed-records> file size {len(data)}b not a "
            f"multiple of stride={stride}"
        )
    grow = (section.grow or "replace").lower()
    return _write(rom, section.offset, data,
                  allow_grow=(grow == "insert"), source=section.source or "")


def _handle_script_windowed(
    rom: bytearray, section: Section, root: Path,
    ctx: Optional[BuildContext] = None,
) -> list[WriteRange]:
    """`script` handler path for `placement.mode = "overflow"`.

    For each `<<<window[N]:$START-$END>>>` block in `file=`, patch an FFC0
    redirect at the window's source offset and write the encoded EN text +
    FFC0-return tail into freespace via `ctx.allocator`. Pointer table stays
    untouched — windows patch inside existing entry bytecodes.
    """
    if section.pointer_table is None or section.count is None:
        raise HandlerError(
            f"{section.source}: script (placement=overflow) requires "
            f"pointer-table= + count="
        )
    if ctx is None or ctx.allocator is None:
        raise HandlerError(
            f"{section.source}: script (placement=overflow) needs "
            f"[rom.build].freespace for overflow allocation"
        )

    from retrotool.script.encode import encode_windowed_script_file
    from retrotool.script.table import Table as _WinTable
    from retrotool.core.address import SFCAddress, SFCAddressType
    from retrotool.build.overflow import get_pointer_encoder

    ptr_size = section.pointer_size or 2
    if ptr_size != 2:
        raise HandlerError(
            f"{section.source}: script (placement=overflow) currently "
            f"supports pointer-size=2 only"
        )

    script_path = _resolve(Path(str(section.files[0])), root)
    table_path = _resolve(Path(str(section.table)), root)
    fallback_path = (
        _resolve(Path(str(section.fallback_table)), root)
        if section.fallback_table else None
    )

    # Source ROM snapshot — ptr table + window content must be read before any
    # writes in case an earlier section mutated the region.
    source_snapshot = bytes(rom)
    tbl = _WinTable(str(table_path))
    ctrl_lengths = tbl.ctrl_lengths

    # Read original pointers (2-byte, bank implicit from ptr_tbl_pos's bank).
    count = int(section.count)
    ptr_tbl_pc = section.pointer_table
    ptr_bank = SFCAddress(ptr_tbl_pc).get_bank_byte(SFCAddressType.LOROM1)
    orig_pcs: list[int] = []
    for i in range(count):
        off = ptr_tbl_pc + i * 2
        addr16 = source_snapshot[off] | (source_snapshot[off + 1] << 8)
        snes = (ptr_bank << 16) | addr16
        pc = SFCAddress(snes, SFCAddressType.LOROM1).get_address(SFCAddressType.PC)
        orig_pcs.append(pc)

    # Forward encoder (inline stub → tail in $C6 freespace): lorom1 ($80+).
    forward_encoder: Callable[[int], bytes] = _pc_to_lorom1_bytes
    # Return encoder (tail → source ROM): lorom0 (no $80) matches lm3.
    return_encoder: Callable[[int], bytes] = _pc_to_lorom0_bytes
    if section.overflow is not None:
        enc_name = section.overflow.get("pointer-encoder")
        if enc_name:
            forward_encoder = get_pointer_encoder(str(enc_name))
        ret_name = section.overflow.get("return-pointer-encoder")
        if ret_name:
            return_encoder = get_pointer_encoder(str(ret_name))

    windowed = encode_windowed_script_file(
        script_path, table_path, fallback_table=fallback_path,
    )

    writes: list[WriteRange] = []
    for i, entry_windows in enumerate(windowed):
        if entry_windows is None or i >= count:
            continue
        entry_pc = orig_pcs[i]
        for start, end, encoded_text in entry_windows:
            if not encoded_text:
                continue
            window_size = end - start
            absorbed_suffix = b''
            # Small-window absorption: extend the window into the trailing
            # [end] or a safe (non-FFC0/FFF0) FF-ctrl code so FFC0 has space.
            if window_size < 6:
                end_byte = source_snapshot[entry_pc + end]
                if end_byte == 0x00:
                    absorbed_suffix = b'\x00'
                    end += 1
                    window_size = end - start
                elif end_byte == 0xFF:
                    code = source_snapshot[entry_pc + end + 1]
                    if code not in (0xC0, 0xF0):
                        cmd_len = ctrl_lengths.get(code, 2)
                        absorbed_suffix = bytes(
                            source_snapshot[entry_pc + end:entry_pc + end + cmd_len]
                        )
                        end += cmd_len
                        window_size = end - start
                if window_size < 6:
                    continue

            # Skip no-op rewrites (encoded identical to source window bytes).
            orig_end = end - len(absorbed_suffix)
            orig_text = bytes(source_snapshot[entry_pc + start + 1:entry_pc + orig_end])
            if encoded_text == orig_text:
                continue

            # Inline FFC0 stub at $start+1 (byte at $start stays in ROM).
            ffc0_pc = entry_pc + start + 1
            _ensure_room(rom, ffc0_pc + 5)
            rom[ffc0_pc:ffc0_pc + 5] = b'\xFF\xC0\xFF\xFF\xFF'
            writes.append(WriteRange(offset=ffc0_pc, length=5))

            # Overflow tail: encoded text + absorbed suffix + FFC0 + return.
            return_pc = entry_pc + end
            overflow_tail = (
                encoded_text + absorbed_suffix
                + b'\xFF\xC0' + return_encoder(return_pc)
            )
            tail_pc = ctx.allocator.alloc(len(overflow_tail))
            _ensure_room(rom, tail_pc + len(overflow_tail))
            rom[tail_pc:tail_pc + len(overflow_tail)] = overflow_tail
            writes.append(WriteRange(offset=tail_pc, length=len(overflow_tail)))

            # Patch the 3-byte placeholder at ffc0_pc+2 with the tail PC.
            rom[ffc0_pc + 2:ffc0_pc + 5] = forward_encoder(tail_pc)

    return writes


HANDLERS: dict[SectionKind, HandlerFn] = {
    SectionKind.REP: handle_rep,
    SectionKind.INS: handle_ins,
    SectionKind.BIN: handle_bin,
    SectionKind.GRAPHICS: handle_graphics,
    SectionKind.SCRIPT: handle_script,
    SectionKind.ASAR: handle_asar,
    SectionKind.PROJECT: handle_project,
    SectionKind.FIXED_RECORDS: handle_fixed_records,
    SectionKind.LIBSFX: handle_libsfx,
    # Back-compat alias: `kind="windowed-script"` routes to the unified
    # script handler. `placement.mode = "overflow"` on `kind="script"` is
    # the preferred form; windowed-script is deprecated and kept so existing
    # TOML specs keep building.
    SectionKind.WINDOWED_SCRIPT: handle_script,
}


def get_handler(kind: SectionKind) -> Optional[HandlerFn]:
    return HANDLERS.get(kind)

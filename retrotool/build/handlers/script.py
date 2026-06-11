"""Script-section handlers: relocate mode (re-pack + rewrite pointer
table), overflow/windowed mode (in-place patching inside <<<window>>>
regions with overflow-strategy spill), and the parallel-gather
script_prepare entry point."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from retrotool.build.script_filter import ScriptFilter
from retrotool.build.spec import Section
from retrotool.core.address import SFCAddressType

from retrotool.build.handlers._base import (
    BuildContext,
    HandlerError,
    WriteRange,
    _PreparedScript,
    _ensure_room,
    _pc_to_lorom0_bytes,
    _pc_to_lorom1_bytes,
    _resolve,
    _resolve_pointer_table_pc,
    _select_16bit_within_bank,
    _select_24bit_encoder,
    _write,
)

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


def _script_prepare_relocate(
    rom_snapshot: bytes, section: Section, root: Path,
    script_filter: Optional[ScriptFilter] = None,
) -> _PreparedScript:
    """Worker-side encode for a relocate-mode <script> section.

    Pure: reads `script_path` + `table_path` + (optionally) a snapshot of
    `rom` for `slot-measure="source-entry"`. No allocator, no labels.
    Returns a `_PreparedScript` consumed by `handle_script()` in the apply
    phase.

    Block/window filters are rejected here: relocate mode rewrites the
    entire pointer table, so selectively rebuilding one entry without
    re-encoding its neighbors would risk pointer drift. Section-level
    filters (no block/window suffix) are honored by the section-level
    `--only` / `--skip` mechanism; they don't reach this code.
    """
    from retrotool.build.driver import section_ids_for_filter
    from retrotool.script.encode import encode_script_file  # deferred

    if script_filter is not None and not script_filter.is_empty():
        ids = section_ids_for_filter(section)
        if (script_filter.has_block_filter(ids)
                or script_filter.has_window_filter(ids)):
            raise HandlerError(
                f"{section.source}: --only block/window filter requires "
                f"placement.mode='overflow' (relocate mode rewrites the "
                f"pointer table — partial rebuild would risk pointer drift). "
                f"Either drop the block selector or switch the section to "
                f"overflow mode."
            )

    script_path = _resolve(Path(str(section.files[0])), root)
    table_path = _resolve(Path(str(section.table)), root)
    fallback_path = (
        _resolve(Path(str(section.fallback_table)), root)
        if section.fallback_table else None
    )

    # Encoder's sub_table_filter expects a PC offset; section.pointer_table may
    # be a raw SNES address from the spec (`offset = "$8586E4"`) or a plain PC
    # offset. Normalize via section.address_type (populated from spec.mapping by
    # the driver), falling back to LoROM1.
    addr_type = section.address_type if section.address_type is not None else SFCAddressType.LOROM1
    sub_table_pc, _ = _resolve_pointer_table_pc(section.pointer_table, addr_type)

    entries = encode_script_file(
        script_path, table_path,
        fallback_table=fallback_path,
        word_wrap=section.word_wrap,
        textbuf_limit=section.textbuf_limit,
        sub_table_filter=sub_table_pc,
    )
    count = int(section.count) if section.count is not None else 0
    while len(entries) < count:
        entries.append((b"\x00", None, [], {}, False))
    entries = entries[:count]

    # Snapshot for slot_measure="source-entry": the slot's effective end is
    # measured by walking the source bytes ctrl-aware, not by next-ptr
    # distance. Snapshot must reflect the rom AS OF prepare time so the
    # source bytes seen here match what the serial path saw before any
    # overlapping section's writes.
    source_snapshot: Optional[bytes] = None
    if section.overflow is not None and (
        str(section.overflow.get("slot-measure") or "").strip()
        == "source-entry"
    ):
        source_snapshot = bytes(rom_snapshot)

    return _PreparedScript(
        mode="relocate",
        entries=entries,
        source_snapshot=source_snapshot,
    )


def script_prepare(
    rom_snapshot: bytes, section: Section, root: Path,
    script_filter: Optional[ScriptFilter] = None,
) -> Optional[_PreparedScript]:
    """Driver-facing: run the worker-eligible encode phase for a script
    section. Returns None for paths that aren't worth (or safe to) parallelize
    (legacy concat mode, missing pointer-table). The caller should fall back
    to running `handle_script` serially when None is returned."""
    if section.table is None or not section.files:
        return None
    mode = _script_placement_mode(section, root)
    if mode == "overflow":
        return _script_prepare_overflow(
            rom_snapshot, section, root, script_filter=script_filter,
        )
    # Relocate mode requires pointer-table + count; legacy concat mode is
    # serial-only.
    if section.pointer_table is None or section.count is None:
        return None
    return _script_prepare_relocate(
        rom_snapshot, section, root, script_filter=script_filter,
    )


def handle_script(
    rom: bytearray, section: Section, root: Path,
    ctx: Optional[BuildContext] = None,
    prepared: Optional[_PreparedScript] = None,
    script_filter: Optional[ScriptFilter] = None,
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

    `prepared` is the optional output of `script_prepare()` run in a worker
    thread — when supplied, the encode phase is skipped and we go straight to
    placement. When None, the encode phase runs inline (serial path).
    """
    if section.table is None:
        raise HandlerError(f"{section.source}: <script> requires table=")
    if not section.files:
        raise HandlerError(f"{section.source}: <script> requires file=")

    if _script_placement_mode(section, root) == "overflow":
        return _handle_script_windowed(
            rom, section, root, ctx,
            prepared=prepared, script_filter=script_filter,
        )
    # Legacy mode: no pointer-table, just concatenate Table.encode_text(line)
    # joined with $00. Kept so pre-phase-6 specs keep working.
    if section.pointer_table is None:
        if section.offset is None:
            raise HandlerError(
                f"{section.source}: <script> requires pointer-table= "
                f"(or offset= for legacy concat mode)"
            )
        from retrotool.script.table import load_table as _load_legacy_table
        tbl = _load_legacy_table(_resolve(Path(str(section.table)), root))
        text = _resolve(Path(str(section.files[0])), root).read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if ln]
        data = b"\x00".join(tbl.encode_text(ln) for ln in lines) + b"\x00"
        grow = (section.grow or "replace").lower()
        return _write(rom, section.offset, data,
                      allow_grow=(grow == "insert"), source=section.source or "")

    if section.count is None:
        raise HandlerError(f"{section.source}: <script> requires count=")

    ptr_size = section.pointer_size or 2
    # pointer_size validity is enforced at Section construction.

    if prepared is None:
        prepared = _script_prepare_relocate(
            bytes(rom), section, root, script_filter=script_filter,
        )
    if prepared.mode != "relocate" or prepared.entries is None:
        raise HandlerError(
            f"{section.source}: prepared payload mode mismatch "
            f"(expected relocate, got {prepared.mode!r})"
        )
    entries = prepared.entries
    count = int(section.count)

    table_path = _resolve(Path(str(section.table)), root)

    # Normalize pointer_table to a PC offset. section.pointer_table may be
    # either a SNES address (`offset = "$8586E4"`) or a PC offset depending
    # on how the spec is written. Use section.address_type (populated by
    # driver from spec mapping) for the conversion, falling back to LoROM1.
    # SFCAddress is also used below for the sentinel-passthrough decode.
    from retrotool.core.address import SFCAddress
    addr_type = section.address_type if section.address_type is not None else SFCAddressType.LOROM1
    ptr_tbl_len = count * ptr_size
    ptr_tbl_pc, _ptr_addr = _resolve_pointer_table_pc(
        section.pointer_table, addr_type,
        rom_len=len(rom), table_len=ptr_tbl_len,
        source=section.source or "<script>",
    )
    ptr_tbl_bank = _ptr_addr.get_bank_byte(addr_type)
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
        from retrotool.script.table import load_table as _load_split_table
        _slot_tbl = _load_split_table(str(table_path))
        # Pass both shapes: `ctrl_table` is the multi-prefix snapshot the
        # newer splitter prefers; `ctrl_lengths` is the legacy single-prefix
        # flat view kept for any user-provided splitter that still reads it.
        # `terminator` rides the same ctx so splitters can honor the
        # section's declared entry-terminator byte (rbshura: 0xFF, LM3: 0x00).
        _ctrl_table_fn = getattr(_slot_tbl, "ctrl_table", None)
        splitter_ctx = {
            "ctrl_lengths": getattr(_slot_tbl, "ctrl_lengths", {}) or {},
            "ctrl_table": _ctrl_table_fn() if callable(_ctrl_table_fn) else None,
            "terminator": (
                section.terminator if section.terminator is not None else 0x00
            ),
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
    # Captured during `_script_prepare_relocate` against the rom-as-of-prepare
    # (so worker scratch matches what serial would have observed); fallback
    # for legacy callers passing prepared without a snapshot.
    _source_snapshot: Optional[bytes] = prepared.source_snapshot
    if slot_measure == "source-entry" and _source_snapshot is None:
        _source_snapshot = bytes(rom)
    # Pointer-encoder applied to `[HHHH@N[:label]]` and `[HHHH@@name]` fixup
    # resolution. Defaults to the section's address-type 24-bit encoder
    # (LoROM1 by default, HiROM when the ROM is HiROM, etc.) so the encoder's
    # 3-byte placeholder resolves correctly regardless of mapping mode.
    # Overridable via `section.overflow.pointer-encoder`.
    fixup_pointer_encoder: Callable[[int], bytes] = _select_24bit_encoder(addr_type)
    if section.overflow is not None:
        enc_name = section.overflow.get("pointer-encoder")
        if enc_name:
            fixup_pointer_encoder = _get_pointer_encoder(str(enc_name))

    # Sentinel passthrough. Source ROMs sometimes pad the ptr table tail with
    # entries that decode outside the mappable window (system-area mirrors,
    # etc.). Those slots hold no real text; re-encoding the script bytes in
    # their place would consume data-region space and shift every subsequent
    # ptr. Detect by decoding the source ptr under the project's address
    # type — if conversion to PC returns None, carry the raw source bytes
    # straight through into the output ptr table and skip the data write.
    # Snapshot before any writes since handlers mutate `rom` in place.
    _ensure_room(rom, ptr_tbl_pc + ptr_tbl_len)
    src_ptr_bytes = bytes(rom[ptr_tbl_pc:ptr_tbl_pc + ptr_tbl_len])
    bank_hi = ptr_tbl_bank if ptr_size == 2 else 0
    sentinel_raw: dict[int, bytes] = {}
    src_pc: dict[int, int] = {}
    # When emitting 24-bit pointers, the source ROM's pointer table is
    # typically still 2-byte (this is the migration case). Reading the source
    # bytes at 3-byte stride produces nonsense (bytes from adjacent old
    # entries get reinterpreted as ill-formed 24-bit ptrs) — half the entries
    # decode to invalid addresses and get marked sentinel, leaving stale
    # source bytes in the output table. The 24-bit fast path doesn't use
    # src_pc or sentinel_raw, so skip the source parse entirely when
    # ptr_size == 3. (Future: add an explicit `source-pointer-size` config
    # for genuine 24-bit→24-bit rebuilds.)
    if ptr_size != 3:
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
            # Decode under the project's mapping with NO LoROM1/LoROM2 fallback:
            # a slot that does not map in addr_type is a genuine sentinel. (The
            # fallback would rescue system-area mirror pointers as valid PCs and
            # mis-pack the table — see af522aa regression.)
            pc = SFCAddress(snes, addr_type, lorom_fallback=False).get_address(SFCAddressType.PC)
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
    #
    # Enforce [data].end as a hard upper bound. Without this, oversize EN
    # content silently runs past data_end and clobbers whatever follows
    # (often the next sibling-section's pointer table — see rbshura, where
    # ~14 scenarios share bank $05 sequentially and scenario_00's overflow
    # corrupted scenario_02's table → cascading crashes).
    data_end_pc: Optional[int] = None
    if section.data_end is not None:
        data_end_pc = SFCAddress(section.data_end, addr_type).get_address(SFCAddressType.PC)
    # Snapshot of the source data region so KEEPING ORIGINAL can restore the
    # untouched JP bytes at src_pc[i] after sequential packing may have
    # clobbered them. Without this, a "kept" entry's pointer still resolves
    # to garbage (corrupted by an earlier entry's write at that PC) — the
    # engine reads no proper control-code prefix → bad VRAM writes → crash.
    data_region_snapshot: Optional[bytes] = None
    data_region_start: Optional[int] = None
    if data_end_pc is not None and src_pc:
        data_region_start = data_start
        data_region_snapshot = bytes(rom[data_start:data_end_pc])
    # Each entry needs to end with the section's terminator byte (rbshura: $FF,
    # LM3: $00). The encoder produces text up to the END opcode but doesn't
    # emit the standalone terminator that the engine's byte-walker requires to
    # know where one entry stops and the next begins. When entries are packed
    # contiguously in relocate mode, the walker reads through entry N right
    # into entry N+1's bytes as one giant entry → control codes processed in
    # the wrong context → bad VRAM writes → crash. Append the terminator after
    # each entry's encoded data to restore the separator.
    term_byte = section.terminator if section.terminator is not None else 0xFF
    cur = data_start
    for i, (enc, orig_addr, ent_fixups, ent_labels, force_overflow) in enumerate(entries):
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

        # Append a separator terminator between entries — but ONLY when the
        # project explicitly configures one (`section.terminator`). Re-packed
        # entries need an explicit separator or the engine's byte-walker reads
        # entry N into N+1 (rbshura: $FF, LM3: $00). When `terminator` is unset
        # we must NOT guess a default: appending a byte corrupts byte-exact
        # round-trip of source data that already carries its own structure —
        # e.g. a 5-byte FF-C0 ctrl with no trailing terminator, where a guessed
        # $FF would even re-open a control sequence. (See test_extract
        # round-trip regressions; default-$FF append was a b7d3564 regression.)
        if section.terminator is not None and enc and enc[-1] != section.terminator:
            enc = enc + bytes([section.terminator])

        # 24-bit pointer fast path: with 3-byte L4 entries the runtime engine
        # follows the pointer to any ROM bank, so each entry can live wherever
        # there is freespace — no [data]-region sequential packing, no source
        # slot to fit into, no KEEPING_ORIGINAL fallback. The pointer table
        # entry resolves to the freespace PC directly. Requires the project
        # to supply a freespace pool via [rom.build].freespace.
        if ptr_size == 3:
            if ctx is None or ctx.allocator is None:
                raise HandlerError(
                    f"{section.source}: pointer-size=3 (24-bit pointers) "
                    f"requires a [rom.build].freespace pool for entry "
                    f"allocation, but no allocator is available."
                )
            alloc_pc = ctx.allocator.alloc(len(enc))
            if alloc_pc is None:
                raise HandlerError(
                    f"{section.source}: pointer-size=3 — entry {i} "
                    f"({len(enc)}B) does not fit any freespace block."
                )
            _ensure_room(rom, alloc_pc + len(enc))
            rom[alloc_pc:alloc_pc + len(enc)] = enc
            writes.append(WriteRange(offset=alloc_pc, length=len(enc)))
            entry_pc[i] = alloc_pc
            ptrs.append(alloc_pc)
            if orig_addr is not None:
                seen_addrs[orig_addr] = alloc_pc
            if i in src_pc:
                seen_src_pc.setdefault(src_pc[i], alloc_pc)
            if ent_labels:
                entry_labels_pc[i] = {n: alloc_pc + off for n, off in ent_labels.items()}
            for fx in ent_fixups:
                pending.append((alloc_pc + fx.offset, fx))
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

        if overflow_strategy is not None and (max_inline < len(enc) or force_overflow):
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
            # Hard-enforce [data].end. Past this boundary lies (typically)
            # the next sibling section's pointer table — silent overrun
            # cascades into wild-pointer crashes at runtime. When the entry
            # would exceed data_end, fall back to keeping the original
            # source pointer (entry stays untranslated, source bytes intact).
            if data_end_pc is not None and pc + len(enc) > data_end_pc:
                if i in src_pc:
                    # Keep the original pointer + original ROM bytes.
                    # Restore the source bytes at src_pc[i] in case an
                    # earlier sequential packing has clobbered them — the
                    # engine will read from src_pc[i] at runtime and must
                    # see the original JP control-code-prefixed entry,
                    # not whatever EN tail happened to land there.
                    if (data_region_snapshot is not None
                            and data_region_start is not None
                            and src_pc[i] >= data_region_start
                            and src_pc[i] < data_end_pc):
                        # Walk source extent: until the section terminator.
                        snap_off = src_pc[i] - data_region_start
                        snap_end = snap_off
                        while (snap_end < len(data_region_snapshot)
                               and data_region_snapshot[snap_end] != term_byte):
                            snap_end += 1
                        snap_end = min(snap_end + 1, len(data_region_snapshot))
                        restore_len = snap_end - snap_off
                        rom[src_pc[i]:src_pc[i] + restore_len] = (
                            data_region_snapshot[snap_off:snap_end]
                        )
                        writes.append(WriteRange(offset=src_pc[i], length=restore_len))
                    entry_pc[i] = src_pc[i]
                    ptrs.append(src_pc[i])
                    if orig_addr is not None:
                        seen_addrs[orig_addr] = src_pc[i]
                    seen_src_pc.setdefault(src_pc[i], src_pc[i])
                    print(
                        f"  WARNING: {section.source} entry {i} ({len(enc)}B) "
                        f"exceeds [data].end ${data_end_pc:06X}; KEEPING ORIGINAL "
                        f"pointer ${src_pc[i]:06X} (entry remains untranslated, "
                        f"JP source bytes restored)."
                    )
                    continue
                else:
                    raise HandlerError(
                        f"{section.source}: entry {i} ({len(enc)}B) would write "
                        f"past [data].end at ${pc:06X}+{len(enc)}=${pc+len(enc):06X} "
                        f"> ${data_end_pc:06X}, and no original src_pc to fall back to."
                    )
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

    # Emit pointer table. Pick the encoder based on the section's address
    # type — LoROM1 (default), HiROM, LoROM0 etc. all need different bank-byte
    # arithmetic. Without this dispatch a HiROM section emitting 24-bit ptrs
    # would write LoROM1-style addresses ($80-$FF or $00-$7F banks with $8000
    # offset) instead of HiROM-style ($C0-$FF banks with full $0000-$FFFF).
    emit_24bit = _select_24bit_encoder(addr_type)
    emit_16bit = _select_16bit_within_bank(addr_type)
    for i, pc in enumerate(ptrs):
        if pc is None:
            raw = sentinel_raw[i]
            rom[ptr_tbl_pc + i * ptr_size:ptr_tbl_pc + (i + 1) * ptr_size] = raw
        elif ptr_size == 3:
            rom[ptr_tbl_pc + i * 3:ptr_tbl_pc + i * 3 + 3] = emit_24bit(pc)
        else:
            ptr16 = emit_16bit(pc)
            rom[ptr_tbl_pc + i * 2] = ptr16 & 0xFF
            rom[ptr_tbl_pc + i * 2 + 1] = (ptr16 >> 8) & 0xFF
    writes.insert(0, WriteRange(offset=ptr_tbl_pc, length=ptr_tbl_len))
    return writes


def _script_prepare_overflow(
    rom_snapshot: bytes, section: Section, root: Path,
    script_filter: Optional[ScriptFilter] = None,
) -> _PreparedScript:
    """Worker-side encode for an overflow-mode <script> section.

    Pure: reads script + table + (optionally) windowed-marker payload, plus a
    snapshot of the ptr table so `orig_pcs` can be resolved without touching
    the live rom in apply phase. No allocator, no labels.

    When `script_filter` carries block/window rules that match this section,
    non-allowed entries are masked to `b"\\x00"` (the auto-window handler
    treats that as "preserve source bytes"), and non-allowed windows are
    dropped from the per-entry windowed list. The pointer table is never
    rewritten in overflow mode, so masking is a clean no-op for the masked
    entries.
    """
    from retrotool.build.driver import section_ids_for_filter
    from retrotool.script.encode import (
        encode_script_file,
        encode_windowed_script_file,
        _read_script_text as _read_text,
    )
    from retrotool.script.table import load_table as _load_win_table
    from retrotool.core.address import SFCAddress

    if section.pointer_table is None or section.count is None:
        # Apply phase will surface the missing-attr error.
        return _PreparedScript(mode="overflow")

    script_path = _resolve(Path(str(section.files[0])), root)
    table_path = _resolve(Path(str(section.table)), root)
    fallback_path = (
        _resolve(Path(str(section.fallback_table)), root)
        if section.fallback_table else None
    )

    tbl = _load_win_table(str(table_path))
    ctrl_lengths = tbl.ctrl_lengths
    ctrl_table = tbl.ctrl_table()
    prep_terminator = (
        section.terminator if section.terminator is not None else 0x00
    )

    # Original pointers — 2-byte, bank implicit from ptr_tbl's bank.
    # Use section.address_type (populated from [rom].mapping by driver) instead
    # of hardcoding LoROM1, so HiROM/SA-1/etc. projects resolve correctly.
    count = int(section.count)
    addr_type = section.address_type if section.address_type is not None else SFCAddressType.LOROM1
    # section.pointer_table is a PC offset — resolved through the shared
    # `_resolve_pointer_table_pc` (single point of truth; see its docstring),
    # which also bounds-checks the table read and diagnoses SNES-authored
    # values with the PC offset to use instead.
    ptr_tbl_pc, ptr_addr = _resolve_pointer_table_pc(
        section.pointer_table, addr_type,
        rom_len=len(rom_snapshot), table_len=count * 2,
        source=section.source or "<script windowed>",
    )
    ptr_bank = ptr_addr.get_bank_byte(addr_type)
    orig_pcs: list[int] = []
    for i in range(count):
        off = ptr_tbl_pc + i * 2
        addr16 = rom_snapshot[off] | (rom_snapshot[off + 1] << 8)
        snes = (ptr_bank << 16) | addr16
        pc = SFCAddress(snes, addr_type).get_address(SFCAddressType.PC)
        orig_pcs.append(pc)

    # Auto-window entries: always encode (covers both pure auto-window files
    # and the non-marker entries in hybrid files; encode_script_file returns
    # b'\x00' for entries containing `<<<window>>>` markers, so the apply
    # phase naturally routes those to the windowed path via the skip set).
    # The encoder's sub_table_filter expects a PC offset, so pass ptr_tbl_pc
    # (the converted form) rather than section.pointer_table which may be a
    # raw SNES address from the spec.
    auto_entries = encode_script_file(
        script_path, table_path,
        fallback_table=fallback_path,
        word_wrap=section.word_wrap,
        textbuf_limit=section.textbuf_limit,
        sub_table_filter=ptr_tbl_pc,
    )
    while len(auto_entries) < count:
        auto_entries.append((b"\x00", None, [], {}, False))
    auto_entries = auto_entries[:count]

    file_text = _read_text(script_path)
    has_window_markers = '<<<window' in file_text
    windowed = None
    if has_window_markers:
        windowed = encode_windowed_script_file(
            script_path, table_path, fallback_table=fallback_path,
        )

    if script_filter is not None and not script_filter.is_empty():
        ids = section_ids_for_filter(section)
        if script_filter.has_block_filter(ids):
            # Mask non-allowed entries to the empty-placeholder sentinel.
            # `_emit_auto_window_writes` short-circuits on `enc == b"\\x00"`
            # (handlers.py: empty-placeholder skip), preserving source ROM
            # bytes for those slots verbatim.
            for i in range(len(auto_entries)):
                if not script_filter.block_allowed(ids, i):
                    auto_entries[i] = (b"\x00", None, [], {}, False)
        if windowed is not None and (
            script_filter.has_block_filter(ids)
            or script_filter.has_window_filter(ids)
        ):
            new_windowed: list = []
            for i, entry_windows in enumerate(windowed):
                if entry_windows is None:
                    new_windowed.append(None)
                    continue
                if not script_filter.block_allowed(ids, i):
                    new_windowed.append(None)
                    continue
                kept = [
                    w for w_idx, w in enumerate(entry_windows)
                    if script_filter.window_allowed(ids, i, w_idx)
                ]
                new_windowed.append(kept if kept else None)
            windowed = new_windowed

    return _PreparedScript(
        mode="overflow",
        auto_entries=auto_entries,
        windowed=windowed,
        orig_pcs=orig_pcs,
        ctrl_lengths=ctrl_lengths,
        ctrl_table=ctrl_table,
        terminator=prep_terminator,
        source_snapshot=bytes(rom_snapshot),
        has_window_markers=has_window_markers,
    )


def _handle_script_windowed(
    rom: bytearray, section: Section, root: Path,
    ctx: Optional[BuildContext] = None,
    prepared: Optional[_PreparedScript] = None,
    script_filter: Optional[ScriptFilter] = None,
) -> list[WriteRange]:
    """`script` handler path for `placement.mode = "overflow"`.

    For each `<<<window[N]:$START-$END>>>` block in `file=`, patch an FFC0
    redirect at the window's source offset and write the encoded EN text +
    FFC0-return tail into freespace via `ctx.allocator`. Pointer table stays
    untouched — windows patch inside existing entry bytecodes.

    `prepared` is the optional output of `script_prepare()` from a worker
    thread — when supplied, the encode phase is skipped.
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

    if prepared is None:
        prepared = _script_prepare_overflow(
            bytes(rom), section, root, script_filter=script_filter,
        )
    if prepared.mode != "overflow" or prepared.auto_entries is None:
        raise HandlerError(
            f"{section.source}: prepared payload mode mismatch "
            f"(expected overflow, got {prepared.mode!r})"
        )
    source_snapshot = prepared.source_snapshot or bytes(rom)
    orig_pcs = prepared.orig_pcs or []
    ctrl_lengths = prepared.ctrl_lengths or {}
    ctrl_table = prepared.ctrl_table
    prep_terminator = prepared.terminator
    count = int(section.count)

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

    # Dispatch by file format. Files with explicit `<<<window[N]:$S-$E>>>`
    # blocks take the hybrid path (auto-window for non-marker entries +
    # per-window FFC0 patcher for marker entries). Plain `<<$BANK:N>>` files
    # take the pure auto-window path. Pointer table stays untouched in both.
    if os.environ.get('RT_DEBUG_AUTO_WIN_ALL'):
        print(f"[dispatch] {section.source}: windowed={prepared.has_window_markers} path={script_path}")
    if prepared.has_window_markers:
        windowed_idx_set = {
            i for i, w in enumerate(prepared.windowed or []) if w is not None
        }
        writes_a = _emit_auto_window_writes(
            rom, section, ctx, source_snapshot, orig_pcs, count,
            script_path, table_path, fallback_path, forward_encoder,
            skip_indices=windowed_idx_set,
            ctrl_lengths=ctrl_lengths,
            ctrl_table=ctrl_table,
            terminator=prep_terminator,
            entries=prepared.auto_entries,
        )
        writes_b = _emit_windowed_marker_writes(
            rom, section, ctx, source_snapshot, ctrl_lengths,
            orig_pcs, count, script_path, table_path, fallback_path,
            forward_encoder, return_encoder,
            windowed=prepared.windowed,
        )
        return writes_a + writes_b
    return _emit_auto_window_writes(
        rom, section, ctx, source_snapshot, orig_pcs, count,
        script_path, table_path, fallback_path, forward_encoder,
        ctrl_lengths=ctrl_lengths,
        ctrl_table=ctrl_table,
        terminator=prep_terminator,
        entries=prepared.auto_entries,
    )


def _emit_windowed_marker_writes(
    rom, section, ctx, source_snapshot, ctrl_lengths,
    orig_pcs, count, script_path, table_path, fallback_path,
    forward_encoder, return_encoder,
    windowed: Optional[list] = None,
) -> list[WriteRange]:
    """Per-window FFC0 patches for files with explicit `<<<window>>>` blocks.

    `windowed` is the precomputed `encode_windowed_script_file` output (from
    `_script_prepare_overflow`); when None we encode inline."""
    if windowed is None:
        from retrotool.script.encode import encode_windowed_script_file
        windowed = encode_windowed_script_file(
            script_path, table_path, fallback_table=fallback_path,
        )

    _rtd = os.environ.get('RT_DEBUG_AUTO_WIN')
    _dbg = _rtd and (_rtd == section.source or _rtd in str(section.source))
    writes: list[WriteRange] = []
    # Per-entry clobber override from `[display].clobber_lead_entries` in
    # the DataDef TOML. Default (entry not listed) = preserve byte at $START
    # (FFC0 stub lands at $START+1, legacy behavior). Listed entries flip
    # to clobber-mode for ALL windows of that entry: FFC0 stub at $START.
    clobber_set = set(getattr(section, 'clobber_lead_entries', None) or [])
    if _dbg:
        print(f"[windowed] {section.source}: {len(windowed)} entries total"
              f"{f', clobber={sorted(clobber_set)}' if clobber_set else ''}")
    for i, entry_windows in enumerate(windowed):
        if entry_windows is None or i >= count:
            if _dbg and entry_windows is not None:
                print(f"  [windowed] entry {i}: skipped (i>=count={count})")
            continue
        entry_pc = orig_pcs[i]
        # preserve_lead is the default (legacy) behavior; flip to clobber
        # for this whole entry if its index is in the TOML override list.
        preserve_lead = (i not in clobber_set)
        if _dbg:
            print(f"  [windowed] entry {i}: slot_pc=0x{entry_pc:06X}, "
                  f"{len(entry_windows)} windows, preserve_lead={preserve_lead}")
        for win_tuple in entry_windows:
            # Encoder emits 3-tuples (legacy) or 4-tuples (forward-compat).
            if len(win_tuple) == 4:
                start, end, encoded_text, _legacy_flag = win_tuple
            else:
                start, end, encoded_text = win_tuple
            if not encoded_text:
                if _dbg: print(f"    win ${start:04X}-${end:04X}: empty encoded_text skip")
                continue
            # FFC0 stub is 5 bytes. With preserve_lead=True it lands at
            # $start+1 (byte at $start kept as original ROM data) — needs
            # window_size >= 6. With preserve_lead=False it lands at $start
            # (clobbers byte at $start) — needs window_size >= 5.
            stub_offset = 1 if preserve_lead else 0
            min_window_size = 6 if preserve_lead else 5
            window_size = end - start
            absorbed_suffix = b''
            # Small-window absorption: extend the window into the trailing
            # [end] or a safe (non-FFC0/FFF0) FF-ctrl code so FFC0 has space.
            if window_size < min_window_size:
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
                if window_size < min_window_size:
                    continue

            # Skip no-op rewrites (encoded identical to source window bytes).
            # Comparison range starts at $start+stub_offset since that's the
            # first byte the stub will clobber.
            orig_end = end - len(absorbed_suffix)
            orig_text = bytes(source_snapshot[entry_pc + start + stub_offset:entry_pc + orig_end])
            if _dbg:
                print(f"    win ${start:04X}-${end:04X} size={window_size} preserve={preserve_lead} enc={encoded_text[:16].hex()}({len(encoded_text)}b) orig={orig_text[:16].hex()}({len(orig_text)}b)")
            if encoded_text == orig_text:
                if _dbg: print("      → no-op skip")
                continue

            # Inline FFC0 stub at $start (default) or $start+1 (preserve mode).
            ffc0_pc = entry_pc + start + stub_offset
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


def _emit_auto_window_writes(
    rom, section, ctx, source_snapshot, orig_pcs, count,
    script_path, table_path, fallback_path, forward_encoder,
    skip_indices: Optional[set[int]] = None,
    ctrl_lengths: Optional[dict[int, int]] = None,
    ctrl_table: Optional[dict] = None,
    terminator: Optional[int] = None,
    entries: Optional[list] = None,
) -> list[WriteRange]:
    """Universal in-place + FFC0 overflow for plain `<<$BANK:N>>` files.

    For each entry: write encoded EN bytes at the original ptr's PC if they
    fit within the source slot (distance to the next entry's PC). Oversize
    entries get a 5-byte `FF C0 <3-byte ptr>` stub at the slot, with the
    full encoded text written to `ctx.allocator` freespace. The pointer
    table is never rewritten — sibling tables sharing the data region
    (e.g. dialog-1..5) keep their references valid.

    `entries` is the precomputed `encode_script_file` output (from
    `_script_prepare_overflow`); when None we encode inline.
    """
    if entries is None:
        from retrotool.script.encode import encode_script_file
        entries = encode_script_file(
            script_path, table_path,
            fallback_table=fallback_path,
            word_wrap=section.word_wrap,
            textbuf_limit=section.textbuf_limit,
            sub_table_filter=section.pointer_table,
        )
        while len(entries) < count:
            entries.append((b"\x00", None, [], {}, False))
        entries = entries[:count]

    # Per-entry slot end = nearest strictly-greater original PC across all
    # entries. Handles non-monotonic / duplicate ptrs that share bodies.
    # Skip sentinel ptrs (None — decoded outside LoROM mappable area).
    sorted_pcs = sorted({p for p in orig_pcs if p is not None})

    # Normalize the ctrl-walk inputs. `ctrl_table` (preferred, multi-prefix)
    # wins; otherwise wrap legacy `ctrl_lengths` as a single 0xFF prefix.
    # `term_byte` defaults to 0x00 (LM3) when the section didn't declare one.
    if ctrl_table is not None:
        _walk_table: dict = ctrl_table
    elif ctrl_lengths is not None:
        _walk_table = {0xFF: (2, dict(ctrl_lengths))}
    else:
        _walk_table = None
    _walk_prefixes = frozenset(_walk_table.keys()) if _walk_table else frozenset()
    term_byte = terminator if terminator is not None else 0x00

    def _measure_source_entry(start_pc: int) -> int:
        # Ctrl-aware walk: advances past <prefix> <cmd> runs using
        # per-prefix lengths, stops at first `term_byte` INCLUSIVE.
        # Used as the slot upper bound for the last entry (no next-ptr
        # distance available).
        if not _walk_table:
            return 0
        pos = start_pc
        end_limit = len(source_snapshot)
        while pos < end_limit:
            b = source_snapshot[pos]
            if b in _walk_prefixes:
                default_len, cmds = _walk_table[b]
                # 1-byte standalone that doubles as the terminator → end.
                if (
                    default_len == 1 and not cmds
                    and b == term_byte
                ):
                    return (pos - start_pc) + 1
                if default_len == 1 and not cmds:
                    pos += 1
                elif pos + 1 < end_limit:
                    pos += cmds.get(source_snapshot[pos + 1], default_len)
                else:
                    pos += default_len
            elif b == term_byte:
                return (pos - start_pc) + 1
            else:
                pos += 1
        return end_limit - start_pc

    def _slot_end(slot_pc: int) -> Optional[int]:
        # Binary-style scan; counts are small (≤512) so linear is fine.
        for p in sorted_pcs:
            if p > slot_pc:
                return p
        # Last entry by PC: no next ptr to bound the slot. Measure the source
        # text length (ctrl-aware walk to 0x00 terminator) so oversized EN
        # encodings get FFC0-redirected instead of overwriting post-terminator
        # data (e.g. a following sub-table sharing the data region).
        src_len = _measure_source_entry(slot_pc)
        return slot_pc + src_len if src_len > 0 else None

    writes: list[WriteRange] = []
    entry_pc: dict[int, int] = {}        # idx → text-engine entry PC (the slot)
    entry_text_pc: dict[int, int] = {}   # idx → PC where encoded bytes start
    entry_labels_pc: dict[int, dict[str, int]] = {}
    pending: list[tuple[int, object]] = []

    _rtd = os.environ.get('RT_DEBUG_AUTO_WIN')
    _dbg = _rtd and (_rtd == section.source or _rtd in str(section.source))
    if os.environ.get('RT_DEBUG_AUTO_WIN_ALL'):
        print(f"[auto_win] section.source={section.source!r}")
    for i, (enc, _orig_addr, ent_fixups, ent_labels, force_overflow) in enumerate(entries):
        slot_pc = orig_pcs[i]
        if slot_pc is None:
            if _dbg:
                print(f"  [auto_win] {section.source} entry {i}: slot_pc=None (sentinel) skip")
            continue
        # Empty placeholder (missing/`[end]`-only entry). Writing `\x00` into
        # the slot would clobber neighboring entries that share the ptr (common
        # when unused ptrs point into another entry's body). Preserve source.
        if enc == b'\x00':
            entry_pc[i] = slot_pc
            entry_text_pc[i] = slot_pc
            if _dbg:
                print(f"  [auto_win] entry {i}: skip (empty placeholder)")
            continue
        if skip_indices is not None and i in skip_indices:
            # Entry has `<<<window>>>` markers — handled by windowed-marker
            # path. Register slot_pc so fixup resolution can target it.
            entry_pc[i] = slot_pc
            entry_text_pc[i] = slot_pc
            if _dbg:
                print(f"  [auto_win] entry {i}: skip (marker-windowed)")
            continue
        end = _slot_end(slot_pc)
        slot_size = (end - slot_pc) if end is not None else len(enc)

        if _dbg:
            src_preview = source_snapshot[slot_pc:slot_pc + min(len(enc), 16)].hex()
            enc_preview = enc[:16].hex()
            print(f"  [auto_win] {section.source} entry {i}: slot_pc=0x{slot_pc:06X} slot_size={slot_size} enc_len={len(enc)} src={src_preview} enc={enc_preview}")

        # Skip no-op writes when encoded matches the source bytes already.
        if enc == bytes(source_snapshot[slot_pc:slot_pc + len(enc)]):
            if _dbg: print("    → no-op (enc matches source)")
            entry_pc[i] = slot_pc
            entry_text_pc[i] = slot_pc
        elif len(enc) <= slot_size and not force_overflow:
            _ensure_room(rom, slot_pc + len(enc))
            rom[slot_pc:slot_pc + len(enc)] = enc
            writes.append(WriteRange(offset=slot_pc, length=len(enc)))
            if _dbg: print(f"    → inline write {len(enc)}b @ 0x{slot_pc:06X}")
            entry_pc[i] = slot_pc
            entry_text_pc[i] = slot_pc
        else:
            # Oversize → FFC0 redirect. Slot must hold the 5-byte stub.
            # When slot < 5, absorption isn't safe in auto-window mode: every
            # byte past slot_pc is owned by an adjacent entry's pointer, and
            # absorbing would clobber its data. Warn loudly and skip — leaves
            # source bytes intact so adjacent ptrs stay valid; the EN content
            # is dropped for this entry (user must review).
            if slot_size < 5:
                print(
                    f"  WARNING: {section.source} entry {i:4d} slot too small "
                    f"({slot_size}b) for FFC0 stub (5b); encoded EN is "
                    f"{len(enc)}b — SKIPPING write, source bytes preserved. "
                    f"Likely an empty JP entry that gained EN content; "
                    f"verify the translation belongs here."
                )
                entry_pc[i] = slot_pc
                entry_text_pc[i] = slot_pc
                continue
            tail_pc = ctx.allocator.alloc(len(enc))
            _ensure_room(rom, tail_pc + len(enc))
            rom[tail_pc:tail_pc + len(enc)] = enc
            writes.append(WriteRange(offset=tail_pc, length=len(enc)))

            stub = b'\xFF\xC0' + forward_encoder(tail_pc)
            _ensure_room(rom, slot_pc + 5)
            rom[slot_pc:slot_pc + 5] = stub
            writes.append(WriteRange(offset=slot_pc, length=5))
            if _dbg: print(f"    → FFC0 stub @ 0x{slot_pc:06X} → tail 0x{tail_pc:06X} ({len(enc)}b)")
            entry_pc[i] = slot_pc
            entry_text_pc[i] = tail_pc

        text_pc = entry_text_pc[i]
        if ent_labels:
            entry_labels_pc[i] = {n: text_pc + off for n, off in ent_labels.items()}
        for fx in ent_fixups:
            pending.append((text_pc + fx.offset, fx))

    # Resolve [FFC0@N] / [FFC0@N:label] / [HHHH@@global] now that all entries
    # are placed. Targets always point at the entry's slot PC (the text
    # engine reads from there — FFC0-redirected entries forward transparently).
    for rom_pc, fx in pending:
        if fx.global_label is not None:
            if ctx is None or fx.global_label not in ctx.labels:
                raise HandlerError(
                    f"{section.source}: [HHHH@@{fx.global_label}] — unknown global label"
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
        addr = forward_encoder(target_pc)
        rom[rom_pc:rom_pc + len(addr)] = addr

    return writes



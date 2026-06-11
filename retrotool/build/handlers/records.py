"""fixed-records handler: fixed-stride record tables (name lists, menu
entries) packed from labeled text fields, with recomputed ptr_writes."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from retrotool.build.spec import Section

from retrotool.build.handlers._base import (
    BuildContext,
    HandlerError,
    WriteRange,
    _read_concat,
    _resolve,
    _write,
)

_FIXED_HEADER_RE = None  # lazy-compiled (see _pack_fixed_records)


def _looks_like_fixed_script(data: bytes) -> bool:
    """Heuristic: text source if UTF-16 LE BOM or ASCII/UTF-8 starting with
    `#` comment, blank lines, or the `<<$HEX:idx.label>>` header marker."""
    if data.startswith(b"\xff\xfe"):
        return True
    head = data[:4096]
    # Strip leading whitespace; require at least one `<<$` header somewhere
    # in the first 4k — raw packed binaries virtually never contain that
    # exact byte sequence.
    return b"<<$" in head


def _coerce_file_offset(v) -> int:
    """Accept either an int or a string like '$1FC5A7' / '0x1FC5A7' / '1FC5A7'
    and return a file-offset int. Mirrors the front-end `_parse_offset`
    semantics; lives here so handler-side fields can reuse it without
    re-importing the parser."""
    if isinstance(v, int):
        return v
    s = str(v).strip().replace("_", "")
    if s.startswith("$"):
        return int(s[1:], 16)
    if s.lower().startswith("0x"):
        return int(s, 16)
    try:
        return int(s, 16)
    except ValueError:
        return int(s, 10)


def _apply_field_ptr_writes(rom: bytearray, section, layout) -> list:
    """For each `<<fields>>` entry that declares `ptr_writes`, write the
    field's runtime address into one or more pointer-table slots. Avoids
    hardcoding the field's location in a separate asar patch, so budget /
    layout changes flow through automatically.

    `layout` is the dict returned by `_pack_fixed_records` mapping each
    label to `(actual_start, actual_len)`. We prefer that over the declared
    `field['start']` because auto-pack mode resolves `start` at pack time.

    Per-field config:

        ptr_writes = [
            { addr = "$1FC5A7", count = 25, size = 2, format = "within-bank" },
        ]

    - `addr`   (required) file offset where the first pointer goes.
    - `count`  (required) how many consecutive pointers to write.
    - `size`   (default 2) pointer width in bytes (2 or 3).
    - `format` (default "within-bank") pointer value format:
                 "within-bank" → low 16 bits of file offset (= within-bank
                 address for HiROM banks $40-$7D / $C0-$FF or LoROM banks).
                 Use 2-byte pointers with this.
                 "file-offset" → raw N-byte little-endian file offset.
                 "snes-24"     → reserved for future SNES-24 conversion
                                 with mapping-mode awareness.

    Returns a list of WriteRange covering every byte written, so the
    gather-parallel driver captures them for re-apply against the real rom
    (writes outside the section's main extent are otherwise lost). Raises
    HandlerError on misconfiguration or out-of-bounds writes.
    """
    write_ranges: list = []
    if not section.fields:
        return write_ranges
    sec_offset = section.offset
    if sec_offset is None:
        return write_ranges
    if layout is None:
        layout = {}
    for field_dict in section.fields:
        ptr_writes = (field_dict.get("ptr_writes")
                      or field_dict.get("pointer_writes")
                      or [])
        if not ptr_writes:
            continue
        label = str(field_dict.get("label", "?"))
        # Prefer the post-pack resolved start (auto-pack-aware) over the
        # declared one. Fall back to declared start (or 0) for callers that
        # don't pass a layout (legacy entry points / unit tests).
        if label in layout:
            field_start = int(layout[label][0])
        else:
            field_start = int(field_dict.get("start", 0))
        field_file_offset = sec_offset + field_start
        for i, pw in enumerate(ptr_writes):
            if not isinstance(pw, dict) or "addr" not in pw or "count" not in pw:
                raise HandlerError(
                    f"{section.source}: field {label!r} ptr_writes[{i}] needs "
                    f"`addr` and `count` keys (got {pw!r})"
                )
            pw_addr = _coerce_file_offset(pw["addr"])
            pw_count = int(pw["count"])
            pw_size = int(pw.get("size", 2))
            pw_format = str(pw.get("format", "within-bank"))
            if pw_size not in (2, 3):
                raise HandlerError(
                    f"{section.source}: field {label!r} ptr_writes[{i}].size "
                    f"must be 2 or 3, got {pw_size}"
                )
            # Compute the pointer value.
            if pw_format == "within-bank":
                value = field_file_offset & 0xFFFF
                if pw_size != 2:
                    raise HandlerError(
                        f"{section.source}: field {label!r} ptr_writes[{i}] "
                        f"format='within-bank' implies size=2; got size={pw_size}"
                    )
            elif pw_format == "file-offset":
                value = field_file_offset
            else:
                raise HandlerError(
                    f"{section.source}: field {label!r} ptr_writes[{i}] "
                    f"unknown format {pw_format!r} (supported: 'within-bank', "
                    f"'file-offset')"
                )
            # Emit `count` copies of the pointer at consecutive offsets.
            encoded = value.to_bytes(pw_size, "little", signed=False)
            total_bytes = pw_count * pw_size
            if pw_addr + total_bytes > len(rom):
                raise HandlerError(
                    f"{section.source}: field {label!r} ptr_writes[{i}] writes "
                    f"{total_bytes}b at {pw_addr:#x} which exceeds ROM size "
                    f"{len(rom):#x}"
                )
            for k in range(pw_count):
                dest = pw_addr + k * pw_size
                rom[dest:dest + pw_size] = encoded
            write_ranges.append(WriteRange(offset=pw_addr, length=total_bytes))
    return write_ranges


def _pack_fixed_records(
    text: str,
    base: bytes,
    *,
    stride: int,
    count: int,
    fields: list[dict],
    table,
    fallback_table,
    source: str,
) -> tuple[bytes, dict[str, tuple[int, int]]]:
    """Pack a `<<$HEX:idx.label>>`-delimited script into `stride * count`
    bytes. Non-field bytes inside each record are preserved from `base`
    (caller passes the existing ROM slice at data_offset..+stride*count).

    Field schema: each dict must have key `label`; `start` and `len` are
    optional with auto-pack semantics:
      * `len` omitted → field auto-sizes to its encoded content length
        (no upper bound except the enclosing block size).
      * `start` omitted → field is placed immediately after the previous
        field in declaration order (first field defaults to start=0).
      * Auto-pack mode requires `count == 1` because each record's per-field
        layout would otherwise depend on its specific content (illegal for
        a fixed-stride table).
    `fill` defaults to 0x20 (space). Entries with idx ≥ count, or label not
    in the schema, raise HandlerError. Returns `(buf, layout)` where layout
    maps each label to its `(actual_start, actual_len)` so callers (e.g.
    `_apply_field_ptr_writes`) can resolve auto-computed offsets without
    re-deriving them."""
    import re as _re

    global _FIXED_HEADER_RE
    if _FIXED_HEADER_RE is None:
        _FIXED_HEADER_RE = _re.compile(r"\$[0-9A-Fa-f]+:(\d+)\.(\w+)")

    # Build label→field lookup; validate field schema once.
    field_by_label: dict[str, dict] = {}
    field_order: list[str] = []
    any_auto = False
    for f in fields:
        if "label" not in f:
            raise HandlerError(
                f"{source}: fixed-records field schema missing label: {f!r}"
            )
        label = str(f["label"])
        decl_start = f.get("start")
        decl_len = f.get("len")
        if decl_start is None or decl_len is None:
            any_auto = True
        field_by_label[label] = {
            "decl_start": None if decl_start is None else int(decl_start),
            "decl_len":   None if decl_len   is None else int(decl_len),
            "fill": int(f.get("fill", 0x20)),
        }
        field_order.append(label)

    if any_auto and count != 1:
        raise HandlerError(
            f"{source}: auto-pack fields (omitting `start` or `len`) require "
            f"count=1; got count={count}. Fixed-stride tables with N>1 "
            f"records need explicit per-field offsets so every record has "
            f"the same layout."
        )

    from retrotool.script.encode import encode_text as _encode_text
    from retrotool.script.table import load_table as _load_t

    tbl = table if hasattr(table, "char_map") else _load_t(str(table))
    fb_tbl = None
    if fallback_table is not None:
        fb_tbl = fallback_table if hasattr(fallback_table, "char_map") else _load_t(str(fallback_table))

    # First pass: encode all text blocks. Store by (idx, label) so we can
    # then resolve per-record layouts in field-declaration order.
    encoded_by_key: dict[tuple[int, str], bytes] = {}
    skipped_labels: set[str] = set()
    for entry in text.split("<<")[1:]:
        if ">>" not in entry:
            continue
        header, _, content = entry.partition(">>")
        if content.startswith("\n"):
            content = content[1:]
        content = content.rstrip("\n\r\t ")
        m = _FIXED_HEADER_RE.match(header)
        if not m:
            continue
        idx = int(m.group(1))
        label = m.group(2)
        if idx >= count:
            raise HandlerError(
                f"{source}: entry {idx} exceeds count={count}"
            )
        if label not in field_by_label:
            # Label belongs to a sibling section, not this one. A single
            # multi-label source (e.g. records carrying both `.weapon` and
            # `.armor`) can feed several fixed-records sections, each
            # declaring only the field(s) it owns. Skip foreign labels here;
            # if NONE match (truly wrong schema / typo'd field), the
            # post-loop guard raises naming the offenders.
            skipped_labels.add(label)
            continue
        encoded, _fixups, _labels = _encode_text(content, tbl, fallback_table=fb_tbl)
        encoded_by_key[(idx, label)] = encoded

    if not encoded_by_key:
        # Nothing matched. If the source carried labelled headers, every one
        # was foreign to this section's schema — almost always a typo'd field
        # label rather than a legitimate multi-section share (that would leave
        # at least one matching label). Name the offending labels.
        if skipped_labels:
            raise HandlerError(
                f"{source}: unknown field label(s) {sorted(skipped_labels)!r} — "
                f"not in this section's declared schema {sorted(field_by_label)!r}. "
                f"Check the [[fields]] labels against the `<<$HEX:idx.label>>` headers."
            )
        raise HandlerError(
            f"{source}: no source entries matched this section's field "
            f"schema (declared: {sorted(field_by_label)!r}). Check the "
            f"[[fields]] labels against the `<<$HEX:idx.label>>` headers."
        )

    # Second pass: resolve each record's layout in field-declaration order
    # so auto-pack `start` chains correctly. For count>1, all fields must
    # have declared start/len (any_auto would have raised above).
    resolved_layout: dict[str, tuple[int, int]] = {}
    auto_stride = 0
    for idx in range(count):
        prev_end = 0
        for label in field_order:
            decl = field_by_label[label]
            encoded = encoded_by_key.get((idx, label), b"")
            actual_start = decl["decl_start"] if decl["decl_start"] is not None else prev_end
            actual_len = decl["decl_len"] if decl["decl_len"] is not None else len(encoded)
            if decl["decl_len"] is not None and len(encoded) > actual_len:
                # Hard-fail on overflow rather than silently truncating.
                # See the rationale on the matching error below the loop.
                raise HandlerError(
                    f"{source}: field {label!r} entry {idx} encodes to "
                    f"{len(encoded)} B but budget is {actual_len} B "
                    f"(overflow by {len(encoded) - actual_len} B). Increase "
                    f"the field's `len` in the DataDef (or omit it to use "
                    f"auto-pack), trim the source text, or split content "
                    f"across additional fields."
                )
            field_end = actual_start + actual_len
            if idx == 0:
                # Record only the first record's layout — callers use it
                # for ptr_writes; subsequent records share the same shape
                # because auto-pack is disallowed for count>1.
                resolved_layout[label] = (actual_start, actual_len)
            if field_end > auto_stride:
                auto_stride = field_end
            prev_end = field_end

    # When `stride` was given by the caller as the auto-derived value (i.e.
    # all fields had explicit len), use it. Otherwise, expand to fit the
    # auto-packed content. Either way, validate against the block boundary.
    effective_stride = stride if stride is not None else auto_stride
    if effective_stride < auto_stride:
        raise HandlerError(
            f"{source}: packed fields span {auto_stride} B but declared "
            f"block_len/stride is {effective_stride} B. Increase block_len "
            f"or shorten the source text."
        )
    if stride is not None and any_auto:
        # Caller declared stride but at least one field is auto-sized.
        # Honor the caller's stride as the *upper bound* — auto-fields can
        # be smaller. Already validated effective_stride >= auto_stride.
        effective_stride = stride

    buf = bytearray(base)
    if len(buf) != effective_stride * count:
        # Caller gave a short base (e.g. ROM smaller than table region).
        # Extend with the first-field fill or 0xFF as a safe default.
        target_len = effective_stride * count
        if len(buf) < target_len:
            buf.extend(b"\xff" * (target_len - len(buf)))
        else:
            buf = buf[:target_len]

    # Third pass: write encoded content + fill into buf at the resolved
    # offsets. Same per-record loop but now buf is sized correctly.
    for idx in range(count):
        prev_end = 0
        for label in field_order:
            decl = field_by_label[label]
            encoded = encoded_by_key.get((idx, label), b"")
            actual_start = decl["decl_start"] if decl["decl_start"] is not None else prev_end
            actual_len = decl["decl_len"] if decl["decl_len"] is not None else len(encoded)
            padded = encoded + bytes([decl["fill"]]) * (actual_len - len(encoded))
            rec_off = idx * effective_stride + actual_start
            buf[rec_off:rec_off + actual_len] = padded
            prev_end = actual_start + actual_len

    return bytes(buf), resolved_layout


def handle_fixed_records(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    """Fixed-stride record table.

    Two source modes, auto-detected:

    1. **Text script** (`.txt` containing `<<$HEX:idx.label>>` headers) —
       encoded record-by-record into `stride * count` bytes using the
       DataDef's `[[fields]]` schema + `[encoding].table_file`. Non-field
       bytes inside each record are preserved from the working ROM so
       stride padding / unmapped fields stay intact.

    2. **Pre-packed binary** (`stride * count` bytes) — written as-is.
       Backwards-compat for asset-pipeline flows that pack records with
       their own tooling.
    """
    if section.offset is None:
        raise HandlerError(f"{section.source}: <fixed-records> requires offset")
    if not section.files:
        raise HandlerError(f"{section.source}: <fixed-records> requires file=…")
    raw = _read_concat(section, root)
    stride = section.stride
    count = section.count
    grow = (section.grow or "replace").lower()
    allow_grow = (grow == "insert")

    # Text-pack path: need a field schema + stride + count. Missing any of
    # these with a text-looking source is an error — users expect retrotool
    # to pack, not misread as binary.
    is_text = _looks_like_fixed_script(raw)
    if is_text:
        if not section.fields:
            raise HandlerError(
                f"{section.source}: fixed-records source {section.files[0]} looks "
                f"like a text script but section has no field schema "
                f"(define [[fields]] in the DataDef)"
            )
        if count is None:
            raise HandlerError(
                f"{section.source}: text-mode fixed-records requires "
                f"count (from DataDef's entries/pointers)"
            )
        # `stride` (= block_len) is optional in auto-pack mode where every
        # field omits `len` — handle_fixed_records derives an effective
        # stride post-pack. Multi-record tables (count>1) still need an
        # explicit stride for uniform per-record layout.
        if stride is None and count != 1:
            raise HandlerError(
                f"{section.source}: text-mode fixed-records with count>1 "
                f"requires stride (from DataDef's block_len). Auto-pack "
                f"(no stride) is only legal for count=1."
            )
        # Grow the working buffer so we can read a base slice. For auto-pack
        # mode (any field omits `start`/`len`), stride may be unknown until
        # after packing — start with whatever the caller declared (None →
        # use `data.end - data.offset` as a reasonable upper bound), grow if
        # needed after pack.
        if stride is not None:
            total = stride * count
        else:
            # Auto-pack with no declared block_len: use the gap between
            # data.offset and data.end (or the rom tail if data.end isn't
            # set) as the available envelope.
            data_end = section.data_end or len(rom)
            total = max(0, data_end - section.offset)
        end = section.offset + total
        if end > len(rom):
            if not allow_grow:
                raise HandlerError(
                    f"{section.source}: table region {section.offset:#x}..{end:#x} "
                    f"exceeds ROM size {len(rom):#x} (use grow='insert')"
                )
            rom.extend(b"\x00" * (end - len(rom)))
        base_slice = bytes(rom[section.offset:end])
        # Decode text respecting UTF-16 LE BOM (lm3-parity).
        if raw.startswith(b"\xff\xfe"):
            text = raw.decode("utf-16")
        else:
            text = raw.decode("utf-8")
        if section.table is None:
            raise HandlerError(
                f"{section.source}: text-mode fixed-records requires an "
                f"[encoding].table_file on the DataDef"
            )
        tbl_path = _resolve(Path(str(section.table)), root)
        fb_path = (
            _resolve(Path(str(section.fallback_table)), root)
            if section.fallback_table else None
        )
        data, layout = _pack_fixed_records(
            text, base_slice,
            stride=stride, count=count,
            fields=section.fields,
            table=tbl_path,
            fallback_table=fb_path,
            source=section.source or "",
        )
        # Enforce the data-region cap. With auto-pack, the encoded content
        # might exceed `data.end - data.offset`; we surface that loudly
        # rather than silently growing into a neighboring section.
        if section.data_end is not None and section.offset + len(data) > section.data_end:
            raise HandlerError(
                f"{section.source}: packed fixed-records content is "
                f"{len(data)} B but the data region "
                f"{section.offset:#x}..{section.data_end:#x} is only "
                f"{section.data_end - section.offset} B (overflow by "
                f"{section.offset + len(data) - section.data_end} B). Expand "
                f"[data].end (and the matching `freespace` split in the "
                f"project file) or trim the source text."
            )
        write_result = _write(rom, section.offset, data,
                              allow_grow=allow_grow, source=section.source or "")
        # Auto-write any pointer-table references declared per field. Lets
        # users avoid hardcoding a field's runtime location in a separate
        # asar patch — if part1's len changes (so part2's offset moves), the
        # pointers update on the next build instead of producing a silently-
        # broken ROM. See `_apply_field_ptr_writes` for the supported config.
        # Each pointer-write returns its own WriteRange so the gather-parallel
        # driver captures those bytes (writes outside the main section extent
        # would otherwise be discarded when re-applied to the real rom).
        ptr_writes = _apply_field_ptr_writes(rom, section, layout)
        if ptr_writes:
            return [write_result, *ptr_writes]
        return write_result

    # Pre-packed binary path.
    if stride is not None and count is not None:
        expected = stride * count
        if len(raw) != expected:
            raise HandlerError(
                f"{section.source}: <fixed-records> file is {len(raw)}b, "
                f"expected stride*count = {stride}*{count} = {expected}b"
            )
    elif stride is not None and len(raw) % stride != 0:
        raise HandlerError(
            f"{section.source}: <fixed-records> file size {len(raw)}b not a "
            f"multiple of stride={stride}"
        )
    return _write(rom, section.offset, raw,
                  allow_grow=allow_grow, source=section.source or "")



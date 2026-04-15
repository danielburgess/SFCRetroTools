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

from retrotool.mbuild.spec import Section, SectionKind


@dataclass
class BuildContext:
    """State shared across section handlers during a single build.

    - `allocator` — freespace bump-allocator fed from `[mbuild].freespace`.
    - `labels` — global label registry: name → PC offset. Populated from
      `[[mbuild.labels]]` at parse time and from sections that declare
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
    from retrotool.mbuild.front_ends.mbxml import parse_mbxml

    sub_spec = parse_mbxml(src_path)
    sub_root = src_path.parent
    if sub_spec.path is not None:
        sub_root = (sub_root / Path(str(sub_spec.path))).resolve()

    for sub in sub_spec.sections:
        if sub.condition is not None:
            from retrotool.mbuild.interpolate import evaluate_condition
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
    """24-bit LoROM1 SNES address, little-endian."""
    bank = ((pc >> 15) & 0x7F) | 0x80
    addr = (pc & 0x7FFF) | 0x8000
    return bytes([addr & 0xFF, (addr >> 8) & 0xFF, bank & 0xFF])


def _pc_to_lorom_within_bank(pc: int) -> int:
    """16-bit within-bank LoROM address (bank implicit, $8000-$FFFF half)."""
    return (pc & 0x7FFF) | 0x8000


def _ensure_room(rom: bytearray, end: int) -> None:
    if end > len(rom):
        rom.extend(b"\x00" * (end - len(rom)))


def handle_script(
    rom: bytearray, section: Section, root: Path,
    ctx: Optional[BuildContext] = None,
) -> list[WriteRange]:
    """Pointer-table-driven script insertion.

    Emits: pointer table at `pointer-table`, then each entry's encoded text
    at `pointer-table + count * pointer-size` onward (sequential, dedupe on
    `orig_addr`). Overflow via `section.overflow` lands entries in freespace
    through `ctx.allocator`. `[HHHH@N[:label]]` and `[HHHH@@name]` fixups
    resolve after every placement is known.
    """
    if section.table is None:
        raise HandlerError(f"{section.source}: <script> requires table=")
    if not section.files:
        raise HandlerError(f"{section.source}: <script> requires file=")
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
    )
    count = int(section.count)
    while len(entries) < count:
        entries.append((b"\x00", None, [], {}))
    entries = entries[:count]

    ptr_tbl_pc = section.pointer_table
    ptr_tbl_len = count * ptr_size
    data_start = ptr_tbl_pc + ptr_tbl_len

    # TODO: overflow. First target (scene-desc-name) has none configured.
    if section.overflow is not None:
        raise HandlerError(
            f"{section.source}: overflow strategy not yet wired in handle_script"
        )

    writes: list[WriteRange] = []
    ptrs: list[int] = []
    entry_pc: dict[int, int] = {}
    entry_labels_pc: dict[int, dict[str, int]] = {}
    pending: list[tuple[int, object]] = []  # (rom_pc_of_placeholder, ScriptFixup)
    seen_addrs: dict[int, int] = {}
    cur = data_start
    for i, (enc, orig_addr, ent_fixups, ent_labels) in enumerate(entries):
        is_dup = (orig_addr is not None and orig_addr in seen_addrs
                  and enc == b"\x00")
        if is_dup:
            pc = seen_addrs[orig_addr]
        else:
            pc = cur
            _ensure_room(rom, pc + len(enc))
            rom[pc:pc + len(enc)] = enc
            writes.append(WriteRange(offset=pc, length=len(enc)))
            entry_pc[i] = pc
            if ent_labels:
                entry_labels_pc[i] = {n: pc + off for n, off in ent_labels.items()}
            for fx in ent_fixups:
                pending.append((pc + fx.offset, fx))
            if orig_addr is not None:
                seen_addrs[orig_addr] = pc
            cur += len(enc)
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
        addr = _pc_to_lorom1_bytes(target_pc)
        _ensure_room(rom, rom_pc + 3)
        rom[rom_pc:rom_pc + 3] = addr

    # Emit pointer table.
    _ensure_room(rom, ptr_tbl_pc + ptr_tbl_len)
    for i, pc in enumerate(ptrs):
        if ptr_size == 3:
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
}


def get_handler(kind: SectionKind) -> Optional[HandlerFn]:
    return HANDLERS.get(kind)

"""Per-`SectionKind` build handlers.

Each handler takes a mutable bytearray (the working ROM body, SMC header stripped)
and a `Section`, and writes its bytes. Handlers return the (offset, length) range
they wrote — the caller uses this to grow the buffer if needed and to summarize
the build.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from retrotool.mbuild.spec import Section, SectionKind


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

def handle_rep(rom: bytearray, section: Section, root: Path) -> WriteRange:
    if section.offset is None:
        raise HandlerError(f"{section.source}: <rep> requires offset")
    data = _read_concat(section, root)
    return _write(rom, section.offset, data, allow_grow=False, source=section.source or "")


def handle_ins(rom: bytearray, section: Section, root: Path) -> WriteRange:
    if section.offset is None:
        raise HandlerError(f"{section.source}: <ins> requires offset")
    data = _read_concat(section, root)
    return _write(rom, section.offset, data, allow_grow=True, source=section.source or "")


def handle_bin(rom: bytearray, section: Section, root: Path) -> WriteRange:
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


def handle_graphics(rom: bytearray, section: Section, root: Path) -> WriteRange:
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


def handle_project(rom: bytearray, section: Section, root: Path) -> WriteRange:
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
        h(rom, sub, sub_root)

    # <project> is non-cacheable; return a zero-length sentinel so the caller
    # doesn't treat the whole ROM as this section's output.
    return WriteRange(offset=0, length=0)


def handle_asar(rom: bytearray, section: Section, root: Path) -> WriteRange:
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


def handle_libsfx(rom: bytearray, section: Section, root: Path) -> WriteRange:
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


def handle_script(rom: bytearray, section: Section, root: Path) -> WriteRange:
    """Basic script encoder: each line of the text file is Table-encoded and
    concatenated with a $00 terminator. Pointer-table emission lands in a later
    phase (needs DataDef integration)."""
    if section.offset is None:
        raise HandlerError(f"{section.source}: <script> requires offset")
    if section.table is None:
        raise HandlerError(f"{section.source}: <script> requires table=… (table file)")
    if not section.files:
        raise HandlerError(f"{section.source}: <script> requires file=… (text source)")

    pre = getattr(section, "_prepared", None)
    if pre is not None:
        grow = (section.grow or "replace").lower()
        return _write(rom, section.offset, pre,
                      allow_grow=(grow == "insert"), source=section.source or "")

    from retrotool.script.table import Table  # heavy import — deferred

    table = Table(_resolve(Path(str(section.table)), root))
    text_path = _resolve(Path(str(section.files[0])), root)
    text = text_path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln]
    encoded = b"\x00".join(table.encode_text(ln) for ln in lines) + b"\x00"

    grow = (section.grow or "replace").lower()
    allow_grow = grow == "insert"
    return _write(rom, section.offset, encoded, allow_grow=allow_grow, source=section.source or "")


# Dispatch table. Kinds without a Phase-2 handler raise via the default.
# Handlers may return a single WriteRange for a contiguous write, or a
# `list[WriteRange]` when a section produces multiple disjoint writes
# (e.g. the script handler emits pointer-table + per-entry inline +
# freespace tails). The build driver normalizes both shapes to a list.
HandlerFn = Callable[[bytearray, Section, Path], "WriteRange | list[WriteRange]"]

def handle_fixed_records(rom: bytearray, section: Section, root: Path) -> WriteRange:
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

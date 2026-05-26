"""Per-`SectionKind` build handlers.

Each handler takes a mutable bytearray (the working ROM body, SMC header stripped)
and a `Section`, and writes its bytes. Handlers return the (offset, length) range
they wrote — the caller uses this to grow the buffer if needed and to summarize
the build.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from retrotool.build.script_filter import ScriptFilter
from retrotool.build.spec import Section, SectionKind
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


def _handle_graphics_png(rom: bytearray, section: Section, root: Path) -> WriteRange:
    """PNG → SuperFamiconv tiles (+ optional projected tilemap), written into the
    ROM. Lets edited word-art / UI graphics round-trip back in at build time.

    Section attrs (all optional unless noted):
      file=          one .png (required)         offset=        tiles dest (required)
      bpp=2|4|8 (4)  color-zero=RRGGBB           no-flip=bool   tile-count=N (pad tiles)
      format=tiles|tilemap (auto: tilemap when map-offset set)
    Tilemap projection (format=tilemap):
      map-offset=    dest of the 16-bit entries (required)
      tile-base=     added to tile indices (VRAM tile slot the DMA targets)
      map-cols=      dest tilemap stride        (default 32)
      map-entries=   dest tilemap entry count   (default 1024)
      map-base-entry= dest entry of top-left cell (default 0)
      priority=bool  force priority bit
      palette-anchors= "P:RRGGBB,P:RRGGBB" — map each SuperFamiconv subpalette
                       (identified by which one contains the anchor colour) to
                       SNES palette number P. Omit → subpalette index used as-is.
    """
    from retrotool.graphics import (
        encode_png, grouped_palette_bytes, png_palette_rgb, project_tilemap,
    )

    if section.offset is None:
        raise HandlerError(f"{section.source}: <graphics> png requires offset")
    if len(section.files) != 1:
        raise HandlerError(f"{section.source}: <graphics> png requires exactly one file=")
    png = _resolve(Path(str(section.files[0])), root)
    if not png.exists():
        raise HandlerError(f"{section.source}: file not found: {png}")

    a = section.attrs
    bpp = _attr_hex(a.get("bpp")) or 4
    colors = _attr_hex(a.get("colors")) or (4 if bpp == 2 else 16)
    palettes = _attr_hex(a.get("palettes")) or 8
    no_flip = (a.get("no-flip") or "").lower() in ("1", "true", "yes", "on")
    # palette-from-png: pack against the indexed PNG's OWN palette order so tile
    # pixel indices line up with a ROM's fixed CGRAM (SuperFamiconv would
    # otherwise re-sort colours). PLTE laid out as [shared idx0] + (colors-1)
    # colours per subpalette; `palettes` selects how many subpalettes to take.
    fixed_palette = None
    if (a.get("palette-from-png") or "").lower() in ("1", "true", "yes", "on"):
        fixed_palette = grouped_palette_bytes(
            png_palette_rgb(png), subpalettes=palettes, colors_per=colors)
    enc = encode_png(png, bpp=bpp, colors=colors, palettes=palettes,
                     color_zero=a.get("color-zero"), no_flip=no_flip,
                     fixed_palette=fixed_palette)

    tile_bytes = bpp * 8
    tiles = enc.tiles
    tile_count = _attr_hex(a.get("tile-count"))
    if tile_count is not None:
        want = tile_count * tile_bytes
        if len(tiles) > want:
            raise HandlerError(
                f"{section.source}: {len(tiles)//tile_bytes} tiles exceed "
                f"tile-count={tile_count} (raise tile-count / the DMA budget, or "
                f"simplify the art / allow flips)")
        tiles = tiles.ljust(want, b"\x00")

    grow = (section.grow or "replace").lower()
    allow_grow = grow == "insert"
    written = [_write(rom, section.offset, tiles, allow_grow=allow_grow,
                      source=section.source or "")]

    map_off = _attr_hex(a.get("map-offset"))
    fmt = (a.get("format") or ("tilemap" if map_off is not None else "tiles")).lower()
    if fmt == "tilemap":
        if map_off is None:
            raise HandlerError(f"{section.source}: format=tilemap requires map-offset")
        # subpalette -> SNES palette via anchor colours
        palette_remap = None
        anchors = a.get("palette-anchors")
        if anchors:
            palette_remap = {}
            for pair in anchors.split(","):
                pnum, _, rgb = pair.strip().partition(":")
                rgb = rgb.strip().lstrip("#")
                target = (int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16))
                for sub in range(8):
                    if target in enc.subpalette_colors(sub):
                        palette_remap[sub] = int(pnum)
                        break
                else:
                    raise HandlerError(
                        f"{section.source}: palette-anchor {pair!r} colour not found "
                        f"in any subpalette")
        # blank source tiles (all pixels transparent) -> leave entry $0000
        skip_tiles = {i for i in range(len(enc.tiles) // tile_bytes)
                      if not any(enc.tiles[i * tile_bytes:(i + 1) * tile_bytes])}
        map_bytes = project_tilemap(
            enc.entries, enc.cols, enc.rows,
            tile_base=_attr_hex(a.get("tile-base")) or 0,
            base_entry=_attr_hex(a.get("map-base-entry")) or 0,
            dest_cols=_attr_hex(a.get("map-cols")) or 32,
            dest_entries=_attr_hex(a.get("map-entries")) or 1024,
            palette_remap=palette_remap,
            force_priority=(a.get("priority") or "").lower() in ("1", "true", "yes", "on"),
            skip_tiles=skip_tiles,
        )
        written.append(_write(rom, map_off, map_bytes, allow_grow=allow_grow,
                              source=section.source or ""))
    return written if len(written) > 1 else written[0]


def handle_graphics(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None) -> WriteRange:
    """Tile/palette/tilemap data.

    Two input modes:
      * `.png` file (or `format=`/`map-offset=` set) — SuperFamiconv encode →
        tiles + optional projected tilemap (see `_handle_graphics_png`).
      * raw planar binary — written through (identity bitplane only; named
        transforms like MBuild's "2bpp-to-1bpp-il" land later).
    """
    if section.offset is None:
        raise HandlerError(f"{section.source}: <graphics> requires offset")
    is_png = bool(section.files) and str(section.files[0]).lower().endswith(".png")
    if is_png or section.attrs.get("format") or section.attrs.get("map-offset"):
        return _handle_graphics_png(rom, section, root)
    data = _read_concat(section, root)
    if not _is_identity_bitplane(section.codec):
        raise HandlerError(
            f"{section.source}: bitplane transform encode={section.codec!r} not yet "
            f"implemented (only raw passthrough supported)"
        )
    grow = (section.grow or "replace").lower()
    allow_grow = grow == "insert"
    return _write(rom, section.offset, data, allow_grow=allow_grow, source=section.source or "")


def _is_identity_bitplane(encode: Optional[str]) -> bool:
    return (encode or "").lower() in {"", "raw", "planar"}


def bitplane_reverse(encode: Optional[str]) -> Callable[[bytes], bytes]:
    """Resolve the reverse-direction bitplane transform (used by extract).

    Only identity passthrough is wired today; named transforms (e.g. MBuild's
    "2bpp-to-1bpp-il") raise until implemented.
    """
    if not _is_identity_bitplane(encode):
        raise HandlerError(f"bitplane transform encode={encode!r} not yet implemented")
    return lambda b: b


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


def handle_asar(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None):
    """Apply an asar patch to the working ROM. Round-trips through a temp file
    because the `asar` CLI operates on disk. Section.attrs format:
      file=patch.asm  (required)
      includes=A|B|C  (optional, |-separated additional include dirs)
      defines=K=V|K=V (optional, |-separated define list)

    Return type varies with caching mode:
      * default (no `cache="1"`) — returns a single WriteRange covering the
        full ROM, matching historical behavior: cache replay (if this kind
        were force-enabled) would overlay the entire ROM including any
        prior-section writes that happened to be baked in at capture time.
        That coupling is why asar isn't in _CACHEABLE_KINDS by default.
      * `cache="1"` — diff-mode. Returns `list[WriteRange]` covering only
        the bytes asar actually changed, so cache replay applies an
        overlay independent of prior-section output. This only stays
        correct if the patch's writes don't *read* ROM bytes whose value
        depends on earlier sections; the opt-in shifts that responsibility
        to the user.
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
    defines = _parse_pipe_kvs(
        "asar define", raw.get("defines") or "", section.source or "",
    )

    before = bytes(rom)
    with tempfile.TemporaryDirectory(prefix="retrotool-asar-") as td:
        tdp = Path(td)
        rom_in = tdp / "in.sfc"
        rom_out = tdp / "out.sfc"
        rom_in.write_bytes(before)
        result = apply_patch(rom_in, AsarPatch(asm_file, includes, defines), rom_out)
        if not result.ok:
            raise HandlerError(f"{section.source}: asar failed:\n{result.log}")
        new_rom = rom_out.read_bytes()

    return _wrap_assembler_writes(
        rom=rom, before=before, new_rom=new_rom,
        section=section, label="asar",
    )


def handle_ca65(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None):
    """Assemble + link a ca65/ld65 source tree into a binary blob, then
    overlay that blob into the working ROM at `offset=`.

    Third assembler in the trio (alongside `<asar>` and `<bass>`) — same
    section-shape philosophy, but ca65 is a two-stage pipeline: ca65
    assembles each `.s` source to an `.o` object, then ld65 links the
    objects against a Map.cfg into a flat binary. We then `memcpy` the
    linker's output into the working ROM at `offset`.

    Section.attrs format:
      file=src.s         (required) entry source — or `files=A.s|B.s|C.s`
      files=A.s|B.s|C.s  (alternative) `|`-separated multi-source list
      config=Map.cfg     (required) ld65 linker config (any layout that
                         emits a flat binary — typical pattern is a single
                         CODE segment loaded into a sized memory area).
      offset=<int>       (required) PC offset in the working ROM where the
                         linker output is written.
      length=<int>       (optional) cap on bytes written. Linker output
                         shorter than `length` is padded with `pad-byte`
                         (default 0x00); longer raises HandlerError unless
                         `allow-truncate="1"` is set.
      pad-byte=<int>     (optional) byte used for `length` padding.
      grow=insert|replace|fail  (optional) `insert` grows the ROM if the
                         write extends past the current end; `replace` is
                         the default and disallows growth.
      includes=A|B|C     (optional) `-I` paths fed to ca65 (.include / .import
                         resolution).
      defines=K=V|K=V    (optional) `-D NAME=VALUE` pairs for ca65.
      cpu=<str>          (optional, default "65816") ca65 `--cpu` argument.
      debug=0|1|2|3      (optional) ld65 debug level — emits `<rom>.sym` /
                         `.map` / `.dbg` next to the **working ROM file**
                         (not the linker temp output) for downstream
                         debugger import.
      lib-paths=A|B|C    (optional) ld65 `--lib-path` entries.
      cfg-paths=A|B|C    (optional) ld65 `--cfg-path` entries.
      allow-truncate="1" (optional) silently truncate linker output longer
                         than `length`. Off by default — overflow is
                         usually a bug.

    Cache: ca65 sections are in `_CACHEABLE_KINDS` because the linker
    output is a deterministic function of (source bytes, includes, config,
    defines, cpu, debug, ca65/ld65 versions). The driver hashes those
    inputs in `_section_cache_key`.
    """
    if section.offset is None:
        raise HandlerError(
            f"{section.source}: <ca65> requires offset= (PC where the "
            f"linker output overlays the working ROM)"
        )

    raw = section.attrs
    config_attr = raw.get("config")
    if not config_attr:
        raise HandlerError(
            f"{section.source}: <ca65> requires config= (ld65 linker "
            f"config; produces the binary blob to overlay)"
        )
    config_path = _resolve(Path(config_attr), root)
    if not config_path.exists():
        raise HandlerError(
            f"{section.source}: ca65 linker config not found: {config_path}"
        )

    # Source files: `file=` (single, mirrors asar/bass) or `files=A|B|C`
    # (multi-source list — ca65 builds many objects, ld65 links them all).
    srcs: list[Path] = []
    if section.files:
        srcs.append(_resolve(Path(str(section.files[0])), root))
    extra_files = (raw.get("files") or "").strip()
    if extra_files:
        for f in extra_files.split("|"):
            f = f.strip()
            if not f:
                continue
            p = _resolve(Path(f), root)
            srcs.append(p)
    if not srcs:
        raise HandlerError(
            f"{section.source}: <ca65> requires file= (or files=A|B|C)"
        )
    for s in srcs:
        if not s.exists():
            raise HandlerError(
                f"{section.source}: ca65 source not found: {s}"
            )

    includes = [
        _resolve(Path(p), root)
        for p in (raw.get("includes") or "").split("|") if p
    ]
    lib_paths = [
        _resolve(Path(p), root)
        for p in (raw.get("lib-paths") or "").split("|") if p
    ]
    cfg_paths = [
        _resolve(Path(p), root)
        for p in (raw.get("cfg-paths") or "").split("|") if p
    ]
    defines = _parse_pipe_kvs(
        "ca65 define", raw.get("defines") or "", section.source or "",
    )
    cpu = (raw.get("cpu") or "65816").strip() or "65816"

    debug_str = (raw.get("debug") or "0").strip()
    try:
        debug_level = int(debug_str)
    except ValueError as e:
        raise HandlerError(
            f"{section.source}: <ca65> debug= must be 0..3, got {debug_str!r}"
        ) from e
    if debug_level not in (0, 1, 2, 3):
        raise HandlerError(
            f"{section.source}: <ca65> debug= must be 0..3, got {debug_level}"
        )

    length_attr = raw.get("length")
    cap_length: Optional[int] = None
    if length_attr is not None:
        try:
            cap_length = int(str(length_attr), 0)
        except ValueError as e:
            raise HandlerError(
                f"{section.source}: <ca65> length= not int: {length_attr!r}"
            ) from e
    pad_byte_attr = raw.get("pad-byte")
    pad_byte = 0x00
    if pad_byte_attr is not None:
        try:
            pad_byte = int(str(pad_byte_attr), 0) & 0xFF
        except ValueError as e:
            raise HandlerError(
                f"{section.source}: <ca65> pad-byte= not int: {pad_byte_attr!r}"
            ) from e
    allow_truncate = (raw.get("allow-truncate") or "").lower() in (
        "1", "true", "yes",
    )
    grow = (section.grow or "replace").lower()

    # ca65/ld65 are bundled by `retrotool[libsfx]` (or the system binaries
    # via `RETROTOOL_USE_SYSTEM_TOOLS=1`). Defer the import so a project
    # that doesn't use ca65 doesn't pay the toolchain-resolver cost.
    import tempfile

    from retrotool._toolchain import ToolchainError
    from retrotool.asm.ca65 import (
        Ca65Assembler, Ca65Error, Ld65Error, Ld65Linker,
    )

    try:
        with tempfile.TemporaryDirectory(prefix="retrotool-ca65-") as td:
            tdp = Path(td)
            asm = Ca65Assembler(
                include_dirs=includes,
                defines=defines,
                cpu=cpu,
                debug=(debug_level >= 3),
            )
            objs: list[Path] = []
            for i, src in enumerate(srcs):
                obj_out = tdp / f"src{i:02d}.o"
                asm.assemble(src, obj_out)
                objs.append(obj_out)

            link_out = tdp / "out.bin"
            linker = Ld65Linker(
                config=config_path,
                lib_dirs=lib_paths,
                cfg_dirs=cfg_paths,
                debug_level=debug_level,
            )
            link_result = linker.link(objs, link_out)
            blob = link_out.read_bytes()
    except (Ca65Error, Ld65Error) as e:
        raise HandlerError(f"{section.source}: ca65/ld65 failed:\n{e}") from e
    except ToolchainError as e:
        raise HandlerError(
            f"{section.source}: ca65/ld65 toolchain not available — "
            f"`pip install retrotool[libsfx]` or place ca65/ld65 on PATH "
            f"({e})"
        ) from e

    # Apply optional length cap / padding.
    if cap_length is not None:
        if len(blob) > cap_length:
            if not allow_truncate:
                raise HandlerError(
                    f"{section.source}: ca65 linker output is {len(blob)} "
                    f"bytes, exceeds length={cap_length} "
                    f"(set allow-truncate=\"1\" to permit)"
                )
            blob = blob[:cap_length]
        elif len(blob) < cap_length:
            blob = blob + bytes([pad_byte]) * (cap_length - len(blob))

    if not blob:
        raise HandlerError(
            f"{section.source}: ca65 produced empty linker output — "
            f"check config segments and source content"
        )

    # Surface debug artifacts next to the build output. The Linker emitted
    # them next to `link_out` (a tempdir path that's about to disappear);
    # copy the bytes back out before the temp tree is cleaned up.
    # Note: `link_result.symfile` etc. are populated only when debug>=1/2/3.
    if link_result.symfile and link_result.symfile.exists():
        # Persist to an attribute on the section's source attrs for the
        # driver to surface in the result. Lightweight — handlers don't
        # currently get a return-channel for sidecar artifacts, so we
        # write directly next to the working-rom file location reachable
        # via root + section.attrs["sym"] when set, or skip when not.
        sym_target_attr = section.attrs.get("sym")
        if sym_target_attr:
            tgt = _resolve(Path(sym_target_attr), root)
            tgt.parent.mkdir(parents=True, exist_ok=True)
            tgt.write_bytes(link_result.symfile.read_bytes())

    return _write(
        rom, int(section.offset), blob,
        allow_grow=(grow == "insert"),
        source=section.source or "",
    )


def handle_bass(rom: bytearray, section: Section, root: Path, ctx: Optional[BuildContext] = None):
    """Apply a bass v18 (ARM9 fork) patch to the working ROM. Mirror of
    `handle_asar`; same temp-file round-trip and cache semantics.

    Section.attrs format:
      file=patch.asm     (required) entry source for bass
      includes=A|B|C     (optional) extra include search dirs (cache-key only)
      defines=K=V|K=V    (optional) `-d` defines (string substitution)
      constants=K=V|K=V  (optional) `-c` constants (numeric symbols)
      strict="1"         (optional) pass `-strict` to bass
      bass-cmd=path      (optional) explicit bass binary path

    Cache semantics match asar:
      * default — single full-ROM WriteRange.
      * `cache="1"` — diff-mode `list[WriteRange]`, with the same caveat
        that the patch must not depend on prior-section ROM bytes.
    """
    if not section.files:
        raise HandlerError(f"{section.source}: <bass> requires file=… (.asm)")

    import tempfile
    from retrotool.asm.patcher import BassPatch, apply_bass_patch

    asm_file = _resolve(Path(str(section.files[0])), root)
    if not asm_file.exists():
        raise HandlerError(f"{section.source}: bass patch not found: {asm_file}")

    raw = section.attrs
    includes = [
        _resolve(Path(p), root) for p in (raw.get("includes") or "").split("|") if p
    ]
    defines = _parse_pipe_kvs(
        "bass define", raw.get("defines") or "", section.source or "",
    )
    constants = _parse_pipe_kvs(
        "bass constant", raw.get("constants") or "", section.source or "",
    )
    strict = (raw.get("strict") or "").lower() in ("1", "true", "yes")
    bass_cmd = (raw.get("bass-cmd") or "bass").strip() or "bass"

    before = bytes(rom)
    with tempfile.TemporaryDirectory(prefix="retrotool-bass-") as td:
        tdp = Path(td)
        rom_in = tdp / "in.sfc"
        rom_out = tdp / "out.sfc"
        rom_in.write_bytes(before)
        result = apply_bass_patch(
            rom_in,
            BassPatch(
                asm_file=asm_file, includes=includes,
                defines=defines, constants=constants, strict=strict,
            ),
            rom_out,
            bass_cmd=bass_cmd,
        )
        if not result.ok:
            raise HandlerError(f"{section.source}: bass failed:\n{result.log}")
        new_rom = rom_out.read_bytes()

    return _wrap_assembler_writes(
        rom=rom, before=before, new_rom=new_rom,
        section=section, label="bass",
    )


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
    # be a raw SNES address from the spec (`offset = "$8586E4"`). Convert via
    # section.address_type (populated from spec.mapping by the driver).
    from retrotool.core.address import SFCAddress, SFCAddressType
    addr_type = section.address_type if section.address_type is not None else SFCAddressType.LOROM1
    sub_table_pc = SFCAddress(section.pointer_table, addr_type).get_address(SFCAddressType.PC)

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
    if ptr_size not in (2, 3):
        raise HandlerError(
            f"{section.source}: pointer-size must be 2 or 3, got {ptr_size}"
        )

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
    from retrotool.core.address import SFCAddress, SFCAddressType
    addr_type = section.address_type if section.address_type is not None else SFCAddressType.LOROM1
    _ptr_addr = SFCAddress(section.pointer_table, addr_type)
    ptr_tbl_pc = _ptr_addr.get_address(SFCAddressType.PC)
    ptr_tbl_bank = _ptr_addr.get_bank_byte(addr_type)
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
            pc = SFCAddress(snes, addr_type).get_address(SFCAddressType.PC)
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

        # Append terminator byte if entry doesn't already end with it. Engines
        # like rbshura's expect a standalone terminator between entries; the
        # encoder drops it because the source `[F7][FF][FB][15]` text already
        # represents the entry "end" semantically via the END opcode.
        if enc and enc[-1] != term_byte:
            enc = enc + bytes([term_byte])

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
            raise HandlerError(
                f"{source}: entry {idx} references unknown field label "
                f"{label!r} (known: {sorted(field_by_label)!r})"
            )
        encoded, _fixups, _labels = _encode_text(content, tbl, fallback_table=fb_tbl)
        encoded_by_key[(idx, label)] = encoded

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
    from retrotool.core.address import SFCAddress, SFCAddressType

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
    # section.pointer_table may be either a SNES address (`offset = "$8586E4"`)
    # or a PC offset depending on how the TOML/MBXML was authored — both work
    # because we always pass it as the input to SFCAddress(value, addr_type)
    # and read out the PC form for indexing.
    count = int(section.count)
    addr_type = section.address_type if section.address_type is not None else SFCAddressType.LOROM1
    ptr_addr = SFCAddress(section.pointer_table, addr_type)
    ptr_bank = ptr_addr.get_bank_byte(addr_type)
    ptr_tbl_pc = ptr_addr.get_address(SFCAddressType.PC)
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


HANDLERS: dict[SectionKind, HandlerFn] = {
    SectionKind.REP: handle_rep,
    SectionKind.INS: handle_ins,
    SectionKind.BIN: handle_bin,
    SectionKind.GRAPHICS: handle_graphics,
    SectionKind.SCRIPT: handle_script,
    SectionKind.ASAR: handle_asar,
    SectionKind.BASS: handle_bass,
    SectionKind.CA65: handle_ca65,
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

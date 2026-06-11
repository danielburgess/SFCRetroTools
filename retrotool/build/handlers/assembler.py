"""Assembler patch handlers: asar, ca65 (+ld65), bass v18."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from retrotool.build.spec import Section

from retrotool.build.handlers._base import (
    BuildContext,
    HandlerError,
    _parse_pipe_kvs,
    _resolve,
    _wrap_assembler_writes,
    _write,
)

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



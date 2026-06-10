"""editor_config — project configuration for the generic script editor.

The script editor (`retrotool edit`, :mod:`retrotool.script.script_editor`)
adapts itself to a translation project by reading the optional ``[editor]``
tables from the project's ``project.toml``. Everything has a sensible default,
so a bare retrotool project (one with ``build_lang`` / ``<lang>_data_dir``
scalars and a ``tables/`` directory) needs **zero** editor-specific
configuration to open and edit its script files.

Schema (all keys optional)::

    [editor]
    lang            = "en"            # default: build_lang, else "en"
    data_dir        = "data/en"       # default: ${lang}_data_dir scalar
    reference_lang  = "jp"            # default: "jp" if a jp dir exists
    reference_dir   = "data/jp"       # default: ${reference_lang}_data_dir
    table           = "tables/game_${lang}.tbl"   # ${lang} substituted;
                                      # default: tables/*_${lang}.tbl glob
    reference_table = "tables/game_jp.tbl"
    file_patterns   = ["*.txt"]       # globs scanned inside data_dir
    cols_per_line   = 24              # dialog width used for overflow checks

    [editor.control_codes]            # defaults match the common F7-FF scheme
    newline      = "FD"               # explicit line break byte
    page_break   = "FE"               # page break byte
    terminator   = "FF"               # end-of-entry byte
    palette_code = "F9"               # speaker-palette opcode ("" disables)
    opcodes      = { F7 = 2, F8 = 2, F9 = 2, FA = 2, FB = 2 }
                                      # opcode -> total length incl. opcode

    [editor.control_codes.subcodes.FC]  # opcodes whose length depends on a
    default = 3                         # sub-command byte
    "02"    = 4

    [editor.preview]                  # pixel-accurate preview; omit the whole
    font             = "fonts/font.bin"   # table for a text-only editor
    font_slot_stride = 64             # bytes per glyph slot in the font bin
    glyph_width      = 8              # rendered glyph size in pixels
    glyph_height     = 16
    bytes_per_glyph  = 32             # bytes actually DMA'd per glyph
    rom              = "out/game.sfc" # default: [rom].name under output_dir
    palette_table    = "0x058313"     # PC offset of the speaker palette table
    palette_count    = 16
    kanji_font_offset = "0x104000"    # PC offset of a 16x16 kanji font
    kanji_glyph_count = 256
    kanji_table      = "tables/kanji.tbl"   # byte -> kanji char mapping
    scale            = 3              # preview zoom factor

    [editor.files."intro"]            # per-file render overrides, keyed by
    cols_per_line = 32                # file stem
    kanji_escape  = true              # page_break byte is a 2-byte kanji
    mixed_width   = true              # half/full-width glyphs at natural size
    dialog_box    = false             # full-screen text, no dialog framing
    forced_wrap   = true              # in-game renderer wraps; skip overflow

Hex values may be written as TOML integers, ``"FD"``, ``"$FD"`` or ``"0xFD"``
strings.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "ControlCodes",
    "EditorConfig",
    "FileRenderOverride",
    "PreviewConfig",
    "load_editor_config",
]


def _parse_hex(value: Any, *, key: str) -> Optional[int]:
    """Coerce a TOML int or hex string ("FD" / "$FD" / "0xFD") to int.

    Returns None for None / "" so callers can express "disabled".
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"[editor] {key}: expected a byte value, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip().lstrip("$").removeprefix("0x").removeprefix("0X")
        try:
            return int(s, 16)
        except ValueError:
            raise ValueError(f"[editor] {key}: not a hex byte: {value!r}") from None
    raise ValueError(f"[editor] {key}: expected int or hex string, got {type(value).__name__}")


@dataclass
class ControlCodes:
    """Byte-level control-code semantics of the game's script engine.

    The defaults are the common "high bytes are opcodes" scheme used by the
    bundled rbshura tooling; any game with different opcodes overrides them
    in ``[editor.control_codes]``.
    """
    newline: Optional[int] = 0xFD
    page_break: Optional[int] = 0xFE
    terminator: Optional[int] = 0xFF
    palette_code: Optional[int] = 0xF9
    # opcode byte -> total instruction length (including the opcode itself)
    opcode_lengths: dict[int, int] = field(
        default_factory=lambda: {0xF7: 2, 0xF8: 2, 0xF9: 2, 0xFA: 2, 0xFB: 2}
    )
    # opcode byte -> {sub_byte: total length}; key None is the default length.
    subcode_lengths: dict[int, dict[Optional[int], int]] = field(
        default_factory=lambda: {0xFC: {0x02: 4, None: 3}}
    )

    def reserved_bytes(self) -> set[int]:
        """Every byte with control meaning — excluded from char tables."""
        out = set(self.opcode_lengths) | set(self.subcode_lengths)
        for b in (self.newline, self.page_break, self.terminator,
                  self.palette_code):
            if b is not None:
                out.add(b)
        return out


@dataclass
class FileRenderOverride:
    """Per-file rendering overrides (``[editor.files."stem"]``)."""
    cols_per_line: Optional[int] = None
    kanji_escape: bool = False
    mixed_width: bool = False
    dialog_box: bool = True
    forced_wrap: bool = False


@dataclass
class PreviewConfig:
    """Pixel-preview inputs. ``font`` is required for any preview at all;
    ``rom`` + ``palette_table`` add per-speaker palettes; the ``kanji_*``
    keys add a 16x16 escape-coded kanji font."""
    font: Optional[Path] = None
    font_slot_stride: int = 64
    glyph_width: int = 8
    glyph_height: int = 16
    bytes_per_glyph: int = 32
    rom: Optional[Path] = None
    palette_table: Optional[int] = None
    palette_count: int = 16
    kanji_font_offset: Optional[int] = None
    kanji_glyph_count: int = 256
    kanji_table: Optional[Path] = None
    scale: int = 3

    @property
    def enabled(self) -> bool:
        return self.font is not None and self.font.exists()


@dataclass
class EditorConfig:
    """Everything the script editor needs to know about one project."""
    root: Path
    project_name: str = "retrotool project"
    lang: str = "en"
    data_dir: Path = Path(".")
    reference_lang: Optional[str] = None
    reference_dir: Optional[Path] = None
    table: Optional[Path] = None
    reference_table: Optional[Path] = None
    file_patterns: list[str] = field(default_factory=lambda: ["*.txt"])
    cols_per_line: int = 24
    control: ControlCodes = field(default_factory=ControlCodes)
    preview: PreviewConfig = field(default_factory=PreviewConfig)
    files: dict[str, FileRenderOverride] = field(default_factory=dict)

    @property
    def state_path(self) -> Path:
        """Per-project editor session state (last entry, folders). JSON,
        project-local so it travels with a checkout; gitignore it."""
        return self.root / ".editor-state.json"


def _lang_data_dirs(data: dict) -> dict[str, str]:
    """Collect the ``<lang>_data_dir`` scalars from a project.toml dict."""
    return {
        k[: -len("_data_dir")].lower(): v
        for k, v in data.items()
        if isinstance(k, str) and k.endswith("_data_dir")
        and isinstance(v, str) and v
    }


def _discover_table(root: Path, lang: str) -> Optional[Path]:
    """Find a .tbl for `lang` without configuration: prefer
    ``tables/*_<lang>.tbl``, fall back to a lone ``tables/*.tbl``."""
    tables = root / "tables"
    if not tables.is_dir():
        return None
    matches = sorted(tables.glob(f"*_{lang}.tbl"))
    if matches:
        return matches[0]
    all_tbl = sorted(tables.glob("*.tbl"))
    if len(all_tbl) == 1:
        return all_tbl[0]
    return None


def _resolve_path(root: Path, value: Optional[str], lang: str) -> Optional[Path]:
    """Make a config path absolute, substituting ``${lang}``."""
    if not value:
        return None
    return (root / value.replace("${lang}", lang)).resolve()


def _load_control_codes(doc: dict) -> ControlCodes:
    cc = ControlCodes()
    if "newline" in doc:
        cc.newline = _parse_hex(doc["newline"], key="control_codes.newline")
    if "page_break" in doc:
        cc.page_break = _parse_hex(doc["page_break"], key="control_codes.page_break")
    if "terminator" in doc:
        cc.terminator = _parse_hex(doc["terminator"], key="control_codes.terminator")
    if "palette_code" in doc:
        cc.palette_code = _parse_hex(doc["palette_code"], key="control_codes.palette_code")
    if "opcodes" in doc:
        ops = doc["opcodes"]
        if not isinstance(ops, dict):
            raise ValueError("[editor.control_codes] opcodes: expected a table")
        cc.opcode_lengths = {
            _parse_hex(k, key=f"opcodes.{k}"): int(v) for k, v in ops.items()
        }
    if "subcodes" in doc:
        subs = doc["subcodes"]
        if not isinstance(subs, dict):
            raise ValueError("[editor.control_codes] subcodes: expected a table")
        out: dict[int, dict[Optional[int], int]] = {}
        for op, lens in subs.items():
            op_b = _parse_hex(op, key=f"subcodes.{op}")
            entry: dict[Optional[int], int] = {}
            for sub, total in lens.items():
                if sub == "default":
                    entry[None] = int(total)
                else:
                    entry[_parse_hex(sub, key=f"subcodes.{op}.{sub}")] = int(total)
            entry.setdefault(None, 3)
            out[op_b] = entry
        cc.subcode_lengths = out
    return cc


def _load_preview(root: Path, doc: dict, default_rom: Optional[Path]) -> PreviewConfig:
    pv = PreviewConfig()
    pv.font = _resolve_path(root, doc.get("font"), "")
    pv.font_slot_stride = int(doc.get("font_slot_stride", pv.font_slot_stride))
    pv.glyph_width = int(doc.get("glyph_width", pv.glyph_width))
    pv.glyph_height = int(doc.get("glyph_height", pv.glyph_height))
    pv.bytes_per_glyph = int(doc.get("bytes_per_glyph", pv.bytes_per_glyph))
    pv.rom = _resolve_path(root, doc.get("rom"), "") or default_rom
    pv.palette_table = _parse_hex(doc.get("palette_table"), key="preview.palette_table")
    pv.palette_count = int(doc.get("palette_count", pv.palette_count))
    pv.kanji_font_offset = _parse_hex(doc.get("kanji_font_offset"),
                                      key="preview.kanji_font_offset")
    pv.kanji_glyph_count = int(doc.get("kanji_glyph_count", pv.kanji_glyph_count))
    pv.kanji_table = _resolve_path(root, doc.get("kanji_table"), "")
    pv.scale = int(doc.get("scale", pv.scale))
    return pv


def _load_file_overrides(doc: dict) -> dict[str, FileRenderOverride]:
    out: dict[str, FileRenderOverride] = {}
    for stem, ov in doc.items():
        if not isinstance(ov, dict):
            raise ValueError(f"[editor.files.{stem}]: expected a table")
        fro = FileRenderOverride()
        if "cols_per_line" in ov:
            fro.cols_per_line = int(ov["cols_per_line"])
        fro.kanji_escape = bool(ov.get("kanji_escape", fro.kanji_escape))
        fro.mixed_width = bool(ov.get("mixed_width", fro.mixed_width))
        fro.dialog_box = bool(ov.get("dialog_box", fro.dialog_box))
        fro.forced_wrap = bool(ov.get("forced_wrap", fro.forced_wrap))
        out[stem] = fro
    return out


def load_editor_config(project_root: Path | str) -> EditorConfig:
    """Build an :class:`EditorConfig` for the project at ``project_root``.

    Reads ``project.toml`` if present; every value falls back to a default
    derived from the project's build configuration (``build_lang``,
    ``<lang>_data_dir``, ``[rom].name`` / ``[rom.build].output_dir``) or to
    a generic constant. A missing or unparseable project.toml yields a
    fully-default config rooted at ``project_root``.

    Raises ValueError on malformed ``[editor]`` values (bad hex, wrong
    types) — a broken config should fail loudly, not silently fall back.
    """
    root = Path(project_root).resolve()
    cfg = EditorConfig(root=root, project_name=root.name)

    pt = root / "project.toml"
    data: dict = {}
    if pt.exists():
        try:
            with pt.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            data = {}

    ed: dict = data.get("editor") or {}
    if not isinstance(ed, dict):
        raise ValueError("[editor]: expected a table")

    # --- language + data dirs (follow the build unless overridden) ---------
    dirs = _lang_data_dirs(data)
    build_lang = data.get("build_lang")
    lang = ed.get("lang") or (build_lang if isinstance(build_lang, str) else "") or "en"
    cfg.lang = lang.lower()

    data_dir = ed.get("data_dir") or dirs.get(cfg.lang)
    if data_dir:
        cfg.data_dir = (root / data_dir).resolve()
    elif (root / "data" / cfg.lang).is_dir():
        cfg.data_dir = (root / "data" / cfg.lang).resolve()
    else:
        cfg.data_dir = root

    ref_lang = ed.get("reference_lang")
    if ref_lang is None and ("jp" in dirs or (root / "data" / "jp").is_dir()):
        ref_lang = "jp"
    if ref_lang:
        cfg.reference_lang = str(ref_lang).lower()
        ref_dir = ed.get("reference_dir") or dirs.get(cfg.reference_lang)
        if ref_dir:
            cfg.reference_dir = (root / ref_dir).resolve()
        elif (root / "data" / cfg.reference_lang).is_dir():
            cfg.reference_dir = (root / "data" / cfg.reference_lang).resolve()

    # --- tables -------------------------------------------------------------
    cfg.table = (_resolve_path(root, ed.get("table"), cfg.lang)
                 or _discover_table(root, cfg.lang))
    if cfg.reference_lang:
        cfg.reference_table = (
            _resolve_path(root, ed.get("reference_table"), cfg.reference_lang)
            or _discover_table(root, cfg.reference_lang)
        )

    # --- misc ----------------------------------------------------------------
    pats = ed.get("file_patterns")
    if pats:
        if not isinstance(pats, list) or not all(isinstance(p, str) for p in pats):
            raise ValueError("[editor] file_patterns: expected a list of globs")
        cfg.file_patterns = list(pats)
    cfg.cols_per_line = int(ed.get("cols_per_line", cfg.cols_per_line))

    rom_tbl = data.get("rom") or {}
    name = rom_tbl.get("name")
    if isinstance(name, str) and name:
        cfg.project_name = name
        out_dir = (rom_tbl.get("build") or {}).get("output_dir", "out")
        default_rom = (root / out_dir / f"{name}.sfc").resolve()
    else:
        default_rom = None

    cfg.control = _load_control_codes(ed.get("control_codes") or {})
    cfg.preview = _load_preview(root, ed.get("preview") or {}, default_rom)
    cfg.files = _load_file_overrides(ed.get("files") or {})
    return cfg

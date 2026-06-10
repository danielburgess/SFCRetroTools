#!/usr/bin/env python3
"""script_editor — pywebview-based script editor for retrotool projects.

Launch with ``retrotool edit [PROJECT_DIR]`` (or
``python -m retrotool.script.script_editor [PROJECT_DIR]``). The editor
adapts to the project via the optional ``[editor]`` tables in its
``project.toml`` — see :mod:`retrotool.script.editor_config` for the full
schema. With no configuration at all it still opens any retrotool
translation project: it follows ``build_lang`` / ``<lang>_data_dir`` to find
the script files, discovers a ``tables/*_<lang>.tbl`` for byte counting, and
runs in text-only mode (no pixel preview) until a font is configured.

What it does:

  - Three-pane UI: script files → entries → editor with side-by-side
    editable text and a read-only reference language (e.g. JP source).
  - Entry bodies use the retrotool script-dump format: ``<<$ADDR:idx[$N]>>``
    or ``<<$ADDR:idx.label>>`` headers, ``[XX]`` bracketed control bytes.
    ``<<<window...>>>`` placement markers round-trip untouched and are
    ignored for preview/byte counting.
  - Live byte counts + width-overflow warnings per entry, driven by the
    project's .tbl table and ``cols_per_line``.
  - Optional pixel-accurate preview when ``[editor.preview]`` provides a
    font binary: glyphs are decoded from the bin (2bpp, configurable
    geometry), speaker palettes are pulled from the built ROM when
    ``palette_table`` is set, and an escape-coded 16x16 kanji font is
    supported for intro-style renderers.
  - Control-code semantics (newline / page break / terminator / opcode
    lengths / speaker-palette opcode) come from ``[editor.control_codes]``;
    the defaults match the common F7-FF scheme.
  - Autosave: debounced (~400 ms) per-entry; writes are atomic via
    temp+rename and preserve each file's original encoding (UTF-16 or
    UTF-8, detected by BOM).
  - Cross-file find / replace, per-project session restore.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from retrotool.script.editor_config import (
    ControlCodes,
    EditorConfig,
    PreviewConfig,
    load_editor_config,
)

if TYPE_CHECKING:
    from PIL import Image

TILE_BYTES = 16              # 8x8 @ 2bpp = 16 B
ATLAS_COLS = 16              # glyph atlas layout (both kana and kanji)

# Neutral 4-color palette used when no ROM/palette_table is configured (or
# extraction fails): off-white text with a blue outline on a dark backdrop.
FALLBACK_PALETTE = [
    (8, 48, 8, 255),         # 0: backdrop slot (unused — see DIALOG_BG)
    (16, 96, 144, 255),      # 1: outline
    (136, 184, 184, 255),    # 2: mid tone
    (240, 248, 248, 255),    # 3: bright text
]

# Approximated dialog-window backdrop. On the SNES the real backdrop is a
# separate BG layer; this stand-in just gives the preview text something
# dark to sit on.
DIALOG_BG = (16, 24, 48, 255)


# ---------------------------------------------------------------------------
# Font / palette extraction (preview assets)
# ---------------------------------------------------------------------------

def _decode_tile_2bpp(buf: bytes, off: int) -> list[int]:
    """Decode one 8x8 2bpp tile (16 bytes) into 64 palette indices."""
    out = [0] * 64
    for r in range(8):
        b0 = buf[off + r * 2]
        b1 = buf[off + r * 2 + 1]
        for c in range(8):
            bit = 7 - c
            out[r * 8 + c] = ((b0 >> bit) & 1) | (((b1 >> bit) & 1) << 1)
    return out


def _bgr555_to_rgba(word: int) -> tuple[int, int, int, int]:
    """SNES 15-bit BGR555 → 32-bit RGBA (3-bit-left-shift for the 5→8 expand)."""
    r = (word & 0x1F) << 3
    g = ((word >> 5) & 0x1F) << 3
    b = ((word >> 10) & 0x1F) << 3
    return (r, g, b, 255)


def load_speaker_palettes(
    rom_bytes: bytes, table_pc: int, n_palettes: int = 16,
) -> list[list[tuple[int, int, int, int]]]:
    """Extract per-speaker 4-color text palettes from the ROM.

    The table layout is interleaved BGR555 words at stride 4 bytes per
    speaker id (adjacent speakers share 2 of their 4 colors). Returns a
    list of ``[backdrop, outline, mid, bright]`` RGBA palettes.
    """
    out = []
    for pid in range(n_palettes):
        base = table_pc + pid * 4
        if base + 8 > len(rom_bytes):
            break
        words = [
            rom_bytes[base + i * 2] | (rom_bytes[base + i * 2 + 1] << 8)
            for i in range(4)
        ]
        out.append([_bgr555_to_rgba(w) for w in words])
    return out


class PreviewAssets:
    """Decoded font atlases + palettes for the pixel preview.

    Built once at startup from a :class:`PreviewConfig`. ``enabled`` is
    False when no usable font is configured — the editor then runs
    text-only (byte counts and overflow checks still work; they only need
    the .tbl table).
    """

    def __init__(self, pv: PreviewConfig):
        self.pv = pv
        self.enabled = False
        self.glyph_w = pv.glyph_width
        self.glyph_h = pv.glyph_height
        # Full-width (kanji) glyphs render at twice the half-width cell.
        self.kanji_w = pv.glyph_width * 2
        self.kanji_h = pv.glyph_height
        self.scale = pv.scale
        self.palettes: list[list[tuple[int, int, int, int]]] = []
        self.atlases: dict[int, "Image.Image"] = {}
        self.kanji_atlas: Optional["Image.Image"] = None

        if not pv.enabled:
            return
        try:
            from PIL import Image  # noqa: F401 — preview needs pillow
        except ImportError:
            print("preview disabled: pillow not installed "
                  "(pip install 'retrotool[editor]')")
            return

        font_buf = pv.font.read_bytes()
        self.glyph_count = max(1, len(font_buf) // pv.font_slot_stride)

        rom: bytes = b""
        if pv.rom is not None and pv.rom.exists():
            rom = pv.rom.read_bytes()
        if rom and pv.palette_table is not None:
            self.palettes = load_speaker_palettes(
                rom, pv.palette_table, pv.palette_count)
        if not self.palettes:
            self.palettes = [FALLBACK_PALETTE]

        for pid, pal in enumerate(self.palettes):
            self.atlases[pid] = self._build_atlas(font_buf, pal)
        if rom and pv.kanji_font_offset is not None:
            self.kanji_atlas = self._build_kanji_atlas(rom, FALLBACK_PALETTE)
        self.enabled = True

    def _build_atlas(self, font_buf: bytes, palette) -> "Image.Image":
        """Decode the half-width font into one atlas image per palette.
        Each glyph slot is ``font_slot_stride`` bytes; only the first
        ``bytes_per_glyph`` are drawn (top tile then bottom tile for the
        default 8x16 geometry). Pixel value 0 renders transparent."""
        from PIL import Image
        pv = self.pv
        gw, gh = self.glyph_w, self.glyph_h
        tiles_per_glyph = max(1, pv.bytes_per_glyph // TILE_BYTES)
        rows = (self.glyph_count + ATLAS_COLS - 1) // ATLAS_COLS
        atlas = Image.new("RGBA", (ATLAS_COLS * gw, rows * gh), (0, 0, 0, 0))
        render_pal = [(0, 0, 0, 0)] + list(palette[1:])
        for gi in range(self.glyph_count):
            base = gi * pv.font_slot_stride
            if base + pv.bytes_per_glyph > len(font_buf):
                break
            gx = (gi % ATLAS_COLS) * gw
            gy = (gi // ATLAS_COLS) * gh
            # Tiles stack vertically inside the glyph cell (8x16 = top+bottom).
            for t in range(tiles_per_glyph):
                tile = _decode_tile_2bpp(font_buf, base + t * TILE_BYTES)
                ty = gy + t * 8
                if ty + 8 > rows * gh:
                    break
                for py in range(8):
                    for px in range(8):
                        atlas.putpixel((gx + px, ty + py),
                                       render_pal[tile[py * 8 + px]])
        return atlas

    def _build_kanji_atlas(self, rom: bytes, palette) -> "Image.Image":
        """Decode the escape-coded kanji font from ROM into a 16-column
        atlas. Each glyph is 4 × 16 B 2bpp tiles in TL/TR/BL/BR order, so
        kanji byte index N → atlas cell (N%16, N//16)."""
        from PIL import Image
        pv = self.pv
        kw, kh = self.kanji_w, self.kanji_h
        rows = (pv.kanji_glyph_count + ATLAS_COLS - 1) // ATLAS_COLS
        atlas = Image.new("RGBA", (ATLAS_COLS * kw, rows * kh), (0, 0, 0, 0))
        render_pal = [(0, 0, 0, 0)] + list(palette[1:])
        for gi in range(pv.kanji_glyph_count):
            off = pv.kanji_font_offset + gi * 64    # 4 tiles × 16 B
            if off + 64 > len(rom):
                break
            quads = [_decode_tile_2bpp(rom, off + q * 16) for q in range(4)]
            gx = (gi % ATLAS_COLS) * kw
            gy = (gi // ATLAS_COLS) * kh
            for q, (dx, dy) in enumerate(((0, 0), (8, 0), (0, 8), (8, 8))):
                for py in range(8):
                    for px in range(8):
                        atlas.putpixel((gx + dx + px, gy + dy + py),
                                       render_pal[quads[q][py * 8 + px]])
        return atlas

    def palette_for(self, palette_id: Optional[int]):
        """Return (atlas, backdrop_rgba) for a speaker palette id; falls back
        to palette 0. Backdrop is the fixed dark stand-in — on the SNES,
        palette slot 0 is transparent and the real backdrop comes from a
        different BG layer."""
        pid = palette_id if palette_id is not None else 0
        if pid not in self.atlases:
            pid = 0
        return self.atlases[pid], DIALOG_BG


# ---------------------------------------------------------------------------
# Table files — char ↔ byte mapping
# ---------------------------------------------------------------------------

def load_char_table(path: Path, reserved: set[int]) -> dict[str, int]:
    """Parse a .tbl into a {char: byte} reverse map for encoding text.

    Format: lines of ``XX=c`` (hex byte = character); ``#`` / ``;``
    comments and ``@`` directives are skipped, as are multi-byte
    (``XXYY=...``) entries — the editor only needs single-byte glyph
    lookups for preview and byte counting. Bytes in ``reserved`` (the
    project's control codes) are never treated as characters. First
    mapping wins on duplicate characters.
    """
    out: dict[str, int] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split(";", 1)[0].strip()
        if not line or line.startswith("#") or line.startswith("@"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if len(k) != 2:
            continue
        try:
            byte = int(k, 16)
        except ValueError:
            continue
        if byte in reserved:
            continue
        if v and v not in out:
            out[v] = byte
    # Space is conventionally byte 0 in tight half-width fonts; only add
    # the convention when the table itself doesn't define a space.
    out.setdefault(" ", 0x00)
    return out


def load_kanji_table(path: Path) -> dict[str, int]:
    """Parse a kanji .tbl (``XX=字`` lines) into a {char: byte} reverse map
    for the escape-coded kanji font. Same comment/duplicate rules as
    :func:`load_char_table`, but no control-byte filtering — the kanji
    index space is separate from the text encoding."""
    out: dict[str, int] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split(";", 1)[0].strip()
        if not line or "=" not in line or line.startswith("@"):
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(k) != 2:
            continue
        try:
            byte = int(k, 16)
        except ValueError:
            continue
        if v and v not in out:
            out[v] = byte
    return out


# ---------------------------------------------------------------------------
# Script file model
# ---------------------------------------------------------------------------

# Two header forms in play:
#   * Pointer-table scripts — `<<$DEC:idx[$DEC]>>`
#   * Fixed-records DataDefs — `<<$HEX:idx.label>>`
# Both share `<<$ANY:idx <suffix> >>`. We keep the suffix in the trailer
# group and replay the original header verbatim on save so each file
# round-trips to the form its handler expects.
ENTRY_HEADER_RE = re.compile(r"<<\$([0-9A-Fa-f]+):(\d+)(\[\$\d+\]|\.\w+)>>")
_ENTRY_SPLIT_RE = re.compile(r"(<<\$[0-9A-Fa-f]+:\d+(?:\[\$\d+\]|\.\w+)>>)\n")


def _sniff_encoding(raw: bytes) -> str:
    """Pick the text encoding for a script file from its BOM. retrotool's
    extractor emits UTF-16 (with BOM); hand-made files are usually UTF-8."""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return "utf-16"
    return "utf-8"


class Scenario:
    """One script file — list of (header, idx, body) entries.

    The file's encoding is detected from its BOM on load and preserved on
    save, so UTF-16 dumps from ``retrotool extract`` and hand-written
    UTF-8 files both round-trip byte-faithfully.
    """
    def __init__(self, path: Path):
        self.path = path
        self.raw: str = ""
        self.encoding: str = "utf-8"
        self.entries: list[dict] = []  # each: {header, idx, body}
        self.reload()

    def reload(self) -> None:
        raw_bytes = self.path.read_bytes()
        self.encoding = _sniff_encoding(raw_bytes)
        self.raw = raw_bytes.decode(self.encoding)
        self.entries = []
        parts = _ENTRY_SPLIT_RE.split(self.raw)
        for i in range(1, len(parts), 2):
            header = parts[i]
            body = parts[i + 1] if i + 1 < len(parts) else ""
            m = ENTRY_HEADER_RE.match(header)
            if not m:
                continue
            body = body.rstrip("\n")
            self.entries.append({
                "header": header,
                "idx": int(m.group(2)),
                "body": body,
            })

    def save_entry(self, entry_idx: int, new_body: str) -> None:
        """Rewrite one entry's body and write the full file atomically."""
        self.entries[entry_idx]["body"] = new_body
        content = "".join(f"{e['header']}\n{e['body']}\n" for e in self.entries)
        # Atomic write: temp file in same dir → rename
        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent),
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding=self.encoding) as f:
                f.write(content)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise


# ---------------------------------------------------------------------------
# Body → glyph indices (with control-code handling)
# ---------------------------------------------------------------------------

# Overflow-mode placement markers (`<<<window 0: $0F8000-$0FFFFF>>>` etc.)
# are build directives, not text — skip them when tokenizing so they don't
# pollute the preview or the byte count. They stay in the body and
# round-trip on save.
_WINDOW_MARKER_RE = re.compile(r"<<<[^>\n]*>>>[ \t]*\n?")


def _tokenize_brackets(body: str) -> list:
    """Tokenize a body string into a stream of items:
      - ('byte', int)   — a [XX] bracketed byte
      - ('char', str)   — a non-bracket character (Latin letter, kana, etc.)
      - ('nl', None)    — a literal newline in the source file

    Bracket contents may be concatenated ``[FC021408]``, colon-separated
    ``[FC:02]`` (legacy dumps), or space-separated ``[FC 02]``; each
    even-length hex run is split into individual bytes. Anything that
    isn't pure hex renders as a literal so the user can see it.
    ``<<<...>>>`` window markers are skipped entirely.
    """
    out = []
    i = 0
    while i < len(body):
        if body.startswith("<<<", i):
            m = _WINDOW_MARKER_RE.match(body, i)
            if m:
                i = m.end()
                continue
        c = body[i]
        if c == "[":
            end = body.find("]", i)
            if end == -1:
                # Stray '[' — render as literal so user can see it
                out.append(("char", c))
                i += 1
                continue
            token = body[i + 1:end]
            parts = [p for p in re.split(r"[:\s]+", token.strip()) if p]
            bytes_out: list[int] = []
            ok = bool(parts)
            for part in parts:
                if (len(part) >= 2 and len(part) % 2 == 0
                        and all(ch in "0123456789abcdefABCDEF" for ch in part)):
                    bytes_out.extend(
                        int(part[j:j + 2], 16) for j in range(0, len(part), 2))
                else:
                    ok = False
                    break
            if ok:
                for b in bytes_out:
                    out.append(("byte", b))
            else:
                # Not a hex-byte bracket — render literal so user sees it.
                out.append(("char", "[" + token + "]"))
            i = end + 1
        elif c == "\n":
            out.append(("nl", None))
            i += 1
        else:
            out.append(("char", c))
            i += 1
    return out


def _control(cfg: Optional[dict]) -> ControlCodes:
    """Pull the project's control codes out of a render-config dict,
    defaulting to the stock F7-FF scheme."""
    if cfg and isinstance(cfg.get("control"), ControlCodes):
        return cfg["control"]
    return ControlCodes()


def body_byte_count(
    body: str,
    char_to_byte: dict[str, int],
    cfg: Optional[dict] = None,
) -> int:
    """Count the encoded byte length of a body — what would land in ROM
    after running the encoder. Chars in `char_to_byte` cost 1 byte; chars
    only in `cfg['kanji_char_to_byte']` (with kanji_escape enabled) cost 2
    (escape + index). Bracket tokens contribute their literal byte count;
    soft newlines and window markers are 0.
    """
    cfg = cfg or {}
    kanji_escape = bool(cfg.get("kanji_escape"))
    kanji_map = cfg.get("kanji_char_to_byte", {})

    total = 0
    for kind, val in _tokenize_brackets(body):
        if kind == "byte":
            total += 1
        elif kind == "char":
            if val in char_to_byte:
                total += 1
            elif kanji_escape and val in kanji_map:
                total += 2  # escape byte + kanji index
            else:
                # Unknown char — count as 1 (placeholder) so the number
                # doesn't lie about an editable file. The encoder will
                # fail with a clear error at build time.
                total += 1
        # 'nl' contributes 0
    return total


def _parse_pages(
    body: str,
    char_to_byte: dict[str, int],
    cfg: Optional[dict] = None,
) -> list[list[list[int]]]:
    """Parse a body into pages of EXPLICIT lines (glyph-index lists) —
    a new line only at the newline byte, a new page only at the page-break
    byte. No soft-wrapping happens here; this is the shared front half of
    body_to_lines (which wraps for display) and body_overflow (which must
    see the pre-wrap widths the game engine actually renders).

    Kanji glyph indices are encoded as ``256 + XX``; half-width glyphs
    stay 0..255.
    """
    cfg = cfg or {}
    ctl = _control(cfg)
    kanji_escape = bool(cfg.get("kanji_escape"))

    pages: list[list[list[int]]] = [[[]]]
    def current_line() -> list[int]: return pages[-1][-1]
    def new_line(): pages[-1].append([])
    def new_page(): pages.append([[]])
    def append_half(b: int): current_line().append(b)
    def append_kanji(xx: int): current_line().append(256 + xx)

    tokens = _tokenize_brackets(body)
    i = 0
    while i < len(tokens):
        kind, val = tokens[i]
        if kind == "nl":
            # Soft newline in source: not a game newline, just a wrap. Skip.
            i += 1
            continue
        if kind == "char":
            # Lookup chain: the script's own table first; with kanji_escape
            # on, unknown chars fall through to the kanji table (2-byte
            # escape emit). Unknown everywhere renders as glyph 0.
            b = char_to_byte.get(val)
            kanji_xx = None
            if b is None and kanji_escape:
                kanji_xx = cfg.get("kanji_char_to_byte", {}).get(val)
            if kanji_xx is not None:
                append_kanji(kanji_xx)
            else:
                append_half(b if b is not None else 0x00)
            i += 1
            continue
        # byte token — dispatch on the project's control codes
        b = val
        if b == ctl.newline:
            new_line()
            i += 1
        elif b == ctl.page_break:
            # Dialog convention: page break. With kanji_escape, the same
            # byte is a 2-byte kanji escape instead (intro convention).
            if kanji_escape:
                i += 1
                if i < len(tokens) and tokens[i][0] == "byte":
                    append_kanji(tokens[i][1])
                    i += 1
            else:
                new_page()
                i += 1
        elif b == ctl.terminator:
            # End of entry — stop rendering
            break
        elif b in ctl.subcode_lengths:
            # Length depends on the sub-command byte that follows.
            lens = ctl.subcode_lengths[b]
            i += 1
            sub = None
            if i < len(tokens) and tokens[i][0] == "byte":
                sub = tokens[i][1]
                i += 1
            total = lens.get(sub, lens.get(None, 3))
            for _ in range(max(0, total - 2)):
                if i < len(tokens) and tokens[i][0] == "byte":
                    i += 1
        elif b in ctl.opcode_lengths:
            # Fixed-length opcode: skip its params, nothing to render.
            i += 1
            for _ in range(max(0, ctl.opcode_lengths[b] - 1)):
                if i < len(tokens) and tokens[i][0] == "byte":
                    i += 1
        else:
            # A bracketed [XX] byte with no visual meaning (raw positioning
            # words, stray opcodes). IGNORE it — renderable glyphs come from
            # literal characters in the body, not [XX] escapes — so control
            # bytes don't show up as phantom spaces.
            i += 1
    return pages


def _line_units(line: list[int]) -> int:
    """Width of a glyph line in half-cell units: half-width glyphs count 1,
    full-width kanji (index ≥ 256) count 2 — the same units
    ``cols_per_line`` is expressed in."""
    return sum(2 if g >= 256 else 1 for g in line)


def body_overflow(
    body: str,
    char_to_byte: dict[str, int],
    cfg: Optional[dict] = None,
) -> dict:
    """Report explicit lines wider than the file's allotted width.

    Most dialog renderers do NOT auto-wrap — a line only breaks at an
    explicit newline byte — so any line wider than `cols_per_line` runs
    off the screen in-game. The preview soft-wraps for legibility, which
    HIDES the problem; this check measures the pre-wrap lines.

    Files whose in-game renderer force-wraps (``forced_wrap`` override)
    can't overflow by width, so they always report clean.

    Returns ``{"limit": cols, "lines": [{"line": n, "units": w}, ...]}``
    where `line` is 1-based and counts explicit lines across the whole
    entry (continuing through page breaks).
    """
    cfg = cfg or {}
    cols = int(cfg.get("cols_per_line", 24))
    if cfg.get("forced_wrap"):
        return {"limit": cols, "lines": []}
    over: list[dict] = []
    n = 0
    for page in _parse_pages(body, char_to_byte, cfg):
        for ln in page:
            n += 1
            units = _line_units(ln)
            if units > cols:
                over.append({"line": n, "units": units})
    return {"limit": cols, "lines": over}


def body_to_lines(
    body: str,
    char_to_byte: dict[str, int],
    cfg: Optional[dict] = None,
) -> list:
    """Parse a body into renderable lines. Returns a list where each item is
    either a list[int] of glyph indices (a line) or None (page-break gutter).

    `cfg` keys: ``cols_per_line`` (soft-wrap width), ``kanji_escape``,
    ``mixed_width`` (kanji count 2 units), ``control`` (ControlCodes),
    ``kanji_char_to_byte``. Kanji glyph indices are ``256 + XX``; the
    renderer dispatches on ``v >= 256``.
    """
    cfg = cfg or {}
    cols = int(cfg.get("cols_per_line", 24))
    pages = _parse_pages(body, char_to_byte, cfg)

    # Flatten + soft-wrap for display. With mixed_width, `cols` is in
    # half-cell units (half-width = 1, kanji = 2), mirroring pixel-wrap
    # renderers; without it every cell is one column.
    mixed_width = bool(cfg.get("mixed_width"))
    flat: list = []
    for pi, page in enumerate(pages):
        if pi > 0:
            flat.append(None)
        for ln in page:
            if not ln:
                flat.append([])
                continue
            if not mixed_width:
                for off in range(0, len(ln), cols):
                    flat.append(ln[off:off + cols])
                continue
            chunk: list[int] = []
            units = 0
            for g in ln:
                w = 2 if g >= 256 else 1
                if units + w > cols and chunk:
                    flat.append(chunk)
                    chunk, units = [], 0
                chunk.append(g)
                units += w
            if chunk:
                flat.append(chunk)
    return flat


def _draw_dialog_box(
    lines: list,                 # list of list[int] glyph rows for one page
    assets: PreviewAssets,
    atlas: "Image.Image",
    backdrop_rgba: tuple[int, int, int, int],
    cols_per_line: int,
    mixed_width: bool = False,
) -> "Image.Image":
    """Render one page (already split — no None entries) to a bordered
    dialog box at 1×. Glyph indices ≥ 256 route to the kanji atlas (blank
    if none is loaded). With ``mixed_width``, half- and full-width glyphs
    draw at their natural pixel widths and ``cols_per_line`` is in
    half-cell units."""
    from PIL import Image
    gw, gh = assets.glyph_w, assets.glyph_h
    kw, kh = assets.kanji_w, assets.kanji_h
    pad = 12
    cell_h = kh if mixed_width else gh
    text_w = cols_per_line * gw
    text_h = max(cell_h, len(lines) * cell_h)
    img_w = text_w + pad * 2
    img_h = text_h + pad * 2
    img = Image.new("RGB", (img_w, img_h), backdrop_rgba[:3])

    # 1 px border around the text area
    for x in range(pad - 2, pad + text_w + 2):
        if 0 <= pad - 2 < img_h:
            img.putpixel((x, pad - 2), (90, 110, 150))
        if 0 <= pad + text_h + 1 < img_h:
            img.putpixel((x, pad + text_h + 1), (90, 110, 150))
    for y in range(pad - 2, pad + text_h + 2):
        if 0 <= pad - 2 < img_w:
            img.putpixel((pad - 2, y), (90, 110, 150))
        if 0 <= pad + text_w + 1 < img_w:
            img.putpixel((pad + text_w + 1, y), (90, 110, 150))

    y = pad
    for line in lines:
        x = pad
        for gi in line:
            if gi >= 256:
                # Full-width kanji glyph from the escape-coded font.
                if assets.kanji_atlas is None:
                    x += kw
                    continue
                kx = gi - 256
                ax = (kx % ATLAS_COLS) * kw
                ay = (kx // ATLAS_COLS) * kh
                glyph = assets.kanji_atlas.crop((ax, ay, ax + kw, ay + kh))
                img.paste(glyph, (x, y), glyph)
                x += kw
            else:
                ax = (gi % ATLAS_COLS) * gw
                ay = (gi // ATLAS_COLS) * gh
                glyph = atlas.crop((ax, ay, ax + gw, ay + gh))
                img.paste(glyph, (x, y), glyph)
                x += gw
        y += cell_h
    return img


def render_preview(
    body: str,
    assets: PreviewAssets,
    atlas: "Image.Image",
    char_to_byte: dict[str, int],
    backdrop_rgba: tuple[int, int, int, int] = DIALOG_BG,
    cfg: Optional[dict] = None,
) -> bytes:
    """Render an entry to a PNG matching the in-game layout.

    `atlas` and `backdrop_rgba` should come from the SAME speaker palette
    (see Bridge.get_entry). Multi-page entries render as separate dialog
    boxes stacked vertically — same visual model as the game, which
    clears between pages.
    """
    from PIL import Image
    cfg = cfg or {}
    cols = int(cfg.get("cols_per_line", 24))
    lines = body_to_lines(body, char_to_byte, cfg)

    pages: list[list[list[int]]] = [[]]
    for ln in lines:
        if ln is None:
            pages.append([])
        else:
            pages[-1].append(ln)
    if not pages or all(not p for p in pages):
        pages = [[[]]]

    GAP = 10
    mixed_width = bool(cfg.get("mixed_width"))
    page_imgs = [
        _draw_dialog_box(p, assets, atlas, backdrop_rgba,
                         cols_per_line=cols, mixed_width=mixed_width)
        for p in pages
    ]
    full_w = max(im.width for im in page_imgs)
    full_h = sum(im.height for im in page_imgs) + GAP * (len(page_imgs) - 1)
    canvas = Image.new("RGB", (full_w, full_h), (10, 10, 22))
    y = 0
    for im in page_imgs:
        canvas.paste(im, (0, y))
        y += im.height + GAP

    if assets.scale != 1:
        canvas = canvas.resize(
            (canvas.width * assets.scale, canvas.height * assets.scale),
            Image.Resampling.NEAREST,
        )
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def extract_palette_id(body: str, control: ControlCodes) -> Optional[int]:
    """Return the speaker-palette id set by the first ``palette_code``
    opcode in the body (drives the preview's glyph color). Handles every
    bracket form by tokenizing: concatenated ``[F904]``, two-bracket
    ``[F9][04]``, and colon ``[F9:04]`` all decode the same. Returns None
    when the project has no palette opcode or the body doesn't use it."""
    if control.palette_code is None:
        return None
    toks = _tokenize_brackets(body)
    for i, (kind, val) in enumerate(toks):
        if kind == "byte" and val == control.palette_code:
            if i + 1 < len(toks) and toks[i + 1][0] == "byte":
                return toks[i + 1][1]
    return None


# ---------------------------------------------------------------------------
# Pywebview bridge
# ---------------------------------------------------------------------------

class Bridge:
    """Exposed to JS via pywebview. All methods return JSON-friendly types.

    NOTE: method signatures must stay positional (no kw-only args) —
    pywebview's JS bridge passes arguments positionally.
    """

    def __init__(self, config: EditorConfig):
        self.config = config
        self.assets = PreviewAssets(config.preview)
        reserved = config.control.reserved_bytes()
        self.char_to_byte = (
            load_char_table(config.table, reserved) if config.table else {}
        )
        self.ref_char_to_byte = (
            load_char_table(config.reference_table, reserved)
            if config.reference_table else {}
        )
        self.kanji_char_to_byte = (
            load_kanji_table(config.preview.kanji_table)
            if config.preview.kanji_table else {}
        )
        self.scenarios: dict[str, Scenario] = {}
        self.ref_scenarios: dict[str, Scenario] = {}
        self._save_timer: Optional[threading.Timer] = None
        self._save_lock = threading.Lock()
        self._pending: dict[tuple[str, int], str] = {}
        # Save state: "idle" → "queued" → "saving" → "saved" / "error".
        # JS polls get_save_state() after queueing a save so the badge can
        # remain in "saving" until Python has actually flushed to disk.
        self._save_state = "idle"
        self._save_error: Optional[str] = None
        self._save_seq = 0  # monotonic; lets JS detect "this save completed"
        # Folder overrides from session state (config defaults if missing).
        st = self.load_session_state()
        self.data_dir = Path(st.get("editable_dir") or st.get("en_dir")
                             or str(config.data_dir))
        ref_default = config.reference_dir
        ref_saved = st.get("reference_dir") or st.get("jp_dir")
        self.reference_dir = Path(ref_saved) if ref_saved else ref_default
        self._reload_scenarios()

    def _reload_scenarios(self) -> None:
        """Re-scan the editable + reference folders. Reference is read-only."""
        self.scenarios = {}
        self.ref_scenarios = {}
        seen: set[Path] = set()
        for pat in self.config.file_patterns:
            for p in sorted(self.data_dir.glob(pat)):
                if p in seen or not p.is_file():
                    continue
                seen.add(p)
                self.scenarios[p.stem] = Scenario(p)
        if self.reference_dir and self.reference_dir.exists():
            seen.clear()
            for pat in self.config.file_patterns:
                for p in sorted(self.reference_dir.glob(pat)):
                    if p in seen or not p.is_file():
                        continue
                    seen.add(p)
                    self.ref_scenarios[p.stem] = Scenario(p)

    # ---- discovery ----
    def list_scenarios(self) -> list[dict]:
        out = []
        for name, sc in self.scenarios.items():
            translated = sum(
                1 for e in sc.entries
                if re.search(r"[A-Za-z]{2,}", e["body"])
            )
            cfg = self._cfg_for(name)
            overflow = sum(
                1 for e in sc.entries
                if body_overflow(e["body"], self.char_to_byte, cfg)["lines"]
            )
            out.append({
                "name": name,
                "entries": len(sc.entries),
                "translated": translated,
                "overflow": overflow,
            })
        return out

    def list_entries(self, scenario_name: str) -> list[dict]:
        sc = self.scenarios.get(scenario_name)
        if sc is None:
            return []
        cfg = self._cfg_for(scenario_name)
        out = []
        for i, e in enumerate(sc.entries):
            preview = re.sub(r"\[[^\]]*\]", "", e["body"]).strip()[:60]
            translated = bool(re.search(r"[A-Za-z]{2,}", e["body"]))
            overflow = bool(
                body_overflow(e["body"], self.char_to_byte, cfg)["lines"]
            )
            out.append({
                "i": i,
                "idx": e["idx"],
                "preview": preview,
                "translated": translated,
                "overflow": overflow,
            })
        return out

    def _cfg_for(self, scenario_name: str) -> dict:
        """Merge the per-file render override with the project's control
        codes + kanji reverse table into the cfg dict the parse/render
        helpers consume."""
        cfg: dict = {
            "cols_per_line": self.config.cols_per_line,
            "control": self.config.control,
        }
        ov = self.config.files.get(scenario_name)
        if ov is not None:
            if ov.cols_per_line is not None:
                cfg["cols_per_line"] = ov.cols_per_line
            cfg["kanji_escape"] = ov.kanji_escape
            cfg["mixed_width"] = ov.mixed_width
            cfg["dialog_box"] = ov.dialog_box
            cfg["forced_wrap"] = ov.forced_wrap
        if cfg.get("kanji_escape"):
            cfg["kanji_char_to_byte"] = self.kanji_char_to_byte
        return cfg

    def _render_png_b64(self, body: str, cfg: dict) -> Optional[str]:
        """Render a body to a base64 data URI, or None when the preview is
        disabled (no font configured)."""
        if not self.assets.enabled:
            return None
        pid = extract_palette_id(body, self.config.control)
        atlas, backdrop = self.assets.palette_for(pid)
        png = render_preview(body, self.assets, atlas, self.char_to_byte,
                             backdrop, cfg=cfg)
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    def get_entry(self, scenario_name: str, entry_i: int) -> dict:
        sc = self.scenarios[scenario_name]
        e = sc.entries[entry_i]
        cfg = self._cfg_for(scenario_name)
        # Look up the reference counterpart by entry index when available.
        ref_body = ""
        if scenario_name in self.ref_scenarios:
            ref_sc = self.ref_scenarios[scenario_name]
            if entry_i < len(ref_sc.entries):
                ref_body = ref_sc.entries[entry_i]["body"]
        # Each side counts bytes with its own table (no cross-language
        # fallback — an EN edit shouldn't borrow JP kana byte values).
        byte_count = body_byte_count(e["body"], self.char_to_byte, cfg)
        ref_byte_count = (
            body_byte_count(ref_body, self.ref_char_to_byte, cfg)
            if ref_body else 0
        )
        return {
            "body": e["body"],
            "jp_body": ref_body,
            "preview_png": self._render_png_b64(e["body"], cfg),
            "byte_count": byte_count,
            "jp_byte_count": ref_byte_count,
            "overflow": body_overflow(e["body"], self.char_to_byte, cfg),
        }

    def render_body(self, body: str, scenario_name: str = "") -> dict:
        """Cheap render-only call (no file write) for live preview.

        Returns {png, byte_count, overflow} so the UI can update preview,
        byte badge and overflow warning from a single round-trip. `png` is
        None in text-only mode."""
        cfg = self._cfg_for(scenario_name) if scenario_name else {
            "cols_per_line": self.config.cols_per_line,
            "control": self.config.control,
        }
        return {
            "png": self._render_png_b64(body, cfg),
            "byte_count": body_byte_count(body, self.char_to_byte, cfg),
            "overflow": body_overflow(body, self.char_to_byte, cfg),
        }

    # ---- autosave (debounced) ----
    def queue_save(self, scenario_name: str, entry_i: int, body: str) -> None:
        with self._save_lock:
            self._pending[(scenario_name, entry_i)] = body
            self._save_state = "queued"
            self._save_error = None
            if self._save_timer is not None:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(0.4, self._flush_pending)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _flush_pending(self) -> None:
        with self._save_lock:
            pending = dict(self._pending)
            self._pending.clear()
            self._save_timer = None
            self._save_state = "saving"
        # Group writes by scenario so each file is written once
        by_scen: dict[str, list[tuple[int, str]]] = {}
        for (name, i), body in pending.items():
            by_scen.setdefault(name, []).append((i, body))
        try:
            for name, items in by_scen.items():
                sc = self.scenarios[name]
                for i, body in items:
                    sc.entries[i]["body"] = body
                # Save once per scenario after applying all queued edits
                sc.save_entry(items[-1][0], items[-1][1])
        except Exception as exc:
            with self._save_lock:
                self._save_state = "error"
                self._save_error = f"{type(exc).__name__}: {exc}"
                self._save_seq += 1
            raise
        with self._save_lock:
            self._save_state = "saved"
            self._save_seq += 1

    # ---- forced sync save (used on window close, manual save) ----
    def flush_now(self) -> None:
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None
        if self._pending:
            self._flush_pending()

    # ---- save state for the UI indicator ----
    def get_save_state(self) -> dict:
        """JS polls this to update the badge. Returns the current state,
        any error message, and a monotonic seq so JS can detect when a
        new save has completed."""
        with self._save_lock:
            return {
                "state": self._save_state,
                "error": self._save_error,
                "seq": self._save_seq,
            }

    # ---- folder settings (editable + reference) ----
    @staticmethod
    def _label_from_dir(path: Optional[Path]) -> str:
        """Display label = folder basename uppercased ('EN' / 'JP' / 'FR'…).
        Em-dash when unset."""
        if path is None:
            return "—"
        name = path.name or path.parent.name
        return name.upper()[:8] or "—"

    def get_settings(self) -> dict:
        return {
            "title": f"{self.config.project_name} — Script Editor",
            "preview_enabled": self.assets.enabled,
            "en_dir": str(self.data_dir),
            "jp_dir": str(self.reference_dir) if self.reference_dir else "",
            "en_dir_exists": self.data_dir.exists(),
            "jp_dir_exists": bool(self.reference_dir
                                  and self.reference_dir.exists()),
            "default_en_dir": str(self.config.data_dir),
            "default_jp_dir": str(self.config.reference_dir or ""),
            # Display labels derived from folder basenames (uppercased) so
            # the UI says 'EN' / 'JP' / 'FR' / … without code edits.
            "editable_label": self._label_from_dir(self.data_dir),
            "reference_label": self._label_from_dir(self.reference_dir),
        }

    def pick_folder(self, initial_path: str = "") -> Optional[str]:
        """Open the OS native folder picker. Returns the chosen path or None
        if the user cancelled. Called by the settings modal's Browse button."""
        try:
            import webview
        except ImportError:
            return None
        if not webview.windows:
            return None
        win = webview.windows[0]
        try:
            initial = initial_path or str(self.data_dir.parent
                                          if self.data_dir.exists()
                                          else self.config.root)
            # FOLDER_DIALOG returns a tuple of paths (or None on cancel).
            result = win.create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=initial,
                allow_multiple=False,
            )
        except Exception:
            return None
        if not result:
            return None
        # pywebview returns either a list/tuple of paths or a single string
        return result[0] if isinstance(result, (list, tuple)) else result

    def set_settings(self, editable_dir: str, reference_dir: str) -> dict:
        """Update the active folder paths and reload scenarios. Persists the
        new paths into the session state file. Returns the new settings dict."""
        if editable_dir:
            self.data_dir = Path(editable_dir).expanduser()
        if reference_dir:
            self.reference_dir = Path(reference_dir).expanduser()
        # Persist into session-state (merge with existing fields)
        st = self.load_session_state()
        st["editable_dir"] = str(self.data_dir)
        st["reference_dir"] = str(self.reference_dir or "")
        self._write_state(st)
        self._reload_scenarios()
        return self.get_settings()

    # ---- cross-scenario find / find-and-replace ----
    def search_text(
        self,
        query: str,
        case_sensitive: bool = False,
        use_regex: bool = False,
        scope: str = "all",      # "all" or a specific scenario name
        side: str = "en",        # "en" (search editable) or "jp" (reference)
    ) -> dict:
        # NOTE: signature kept positional (no `*`) because pywebview's JS
        # bridge passes arguments positionally — kw-only args would 500.
        """Search entry bodies for `query`. Returns matches as a list of
        {scenario, entry_i, start, end, before, match, after} per hit.

        Up to 500 matches returned (UI hard-cap). `error` set if the regex
        was invalid."""
        try:
            if use_regex:
                pat = re.compile(query, 0 if case_sensitive else re.IGNORECASE)
            else:
                pat = re.compile(
                    re.escape(query), 0 if case_sensitive else re.IGNORECASE,
                )
        except re.error as e:
            return {"matches": [], "error": str(e)}

        scenarios = self.scenarios if side == "en" else self.ref_scenarios
        if scope != "all":
            scenarios = {k: v for k, v in scenarios.items() if k == scope}

        matches: list[dict] = []
        for name, sc in scenarios.items():
            for i, e in enumerate(sc.entries):
                body = e["body"]
                for m in pat.finditer(body):
                    s, ee = m.span()
                    matches.append({
                        "scenario": name,
                        "entry_i": i,
                        "start": s,
                        "end": ee,
                        "before": body[max(0, s - 24):s],
                        "match": body[s:ee],
                        "after": body[ee:min(len(body), ee + 24)],
                    })
                    if len(matches) >= 500:
                        return {"matches": matches, "truncated": True, "error": None}
        return {"matches": matches, "truncated": False, "error": None}

    def replace_text(
        self,
        scenario_name: str,
        entry_i: int,
        start: int,
        end: int,
        replacement: str,
    ) -> dict:
        """Replace [start:end] of one entry's body with `replacement`, then
        queue the save. Returns the new full body. Used by JS for the
        find-and-replace path."""
        sc = self.scenarios[scenario_name]
        e = sc.entries[entry_i]
        new_body = e["body"][:start] + replacement + e["body"][end:]
        self.queue_save(scenario_name, entry_i, new_body)
        return {"body": new_body}

    # ---- session persistence (last scenario / entry / cursor) ----
    def load_session_state(self) -> dict:
        """Read the persisted editor state. Returns {} on first launch
        or if the file is missing / corrupt. Schema:
          {scenario, entry_i, cursor, scroll, editable_dir, reference_dir}
        """
        try:
            return json.loads(
                self.config.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_state(self, payload: dict) -> None:
        """Atomic best-effort write of the session-state JSON."""
        state_path = self.config.state_path
        try:
            fd, tmp = tempfile.mkstemp(
                dir=str(state_path.parent),
                prefix=f".{state_path.name}.",
                suffix=".tmp",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, state_path)
        except Exception:
            # Silent — losing session state shouldn't disrupt editing.
            pass

    def save_session_state(self, scenario: str, entry_i: int,
                            cursor: int = 0, scroll: int = 0) -> None:
        """Persist the current selection. Best-effort — failures are
        non-fatal (just lost state on next launch)."""
        st = self.load_session_state()
        st.update({
            "scenario": scenario,
            "entry_i": entry_i,
            "cursor": cursor,
            "scroll": scroll,
        })
        self._write_state(st)


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Script Editor</title>
<style>
:root {
  --bg: #101028; --bg2: #161638; --bg3: #0f3460; --fg: #e8e8f0;
  --fg2: #8888a0; --acc: #e94560; --grn: #4ecca3; --yel: #f0c040;
  --brd: #2a2a4e; --code: 'Cascadia Mono', Consolas, monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--fg);
       display: grid; grid-template-columns: 220px 280px 1fr; height: 100vh; overflow: hidden; }
.col { display: flex; flex-direction: column; overflow: hidden; border-right: 1px solid var(--brd); }
.col:last-child { border-right: none; }
.col h2 { padding: 10px 12px; background: var(--bg2); font-size: 11px;
          text-transform: uppercase; letter-spacing: 1px; color: var(--acc);
          border-bottom: 1px solid var(--brd); }
.list { flex: 1; overflow-y: auto; }
.item { padding: 8px 12px; cursor: pointer; border-bottom: 1px solid var(--brd); font-size: 13px; }
.item:hover { background: var(--bg2); }
.item.active { background: var(--bg3); color: #fff; border-left: 3px solid var(--acc); padding-left: 9px; }
.item .meta { color: var(--fg2); font-size: 11px; margin-top: 2px; }
.item.translated .marker { color: var(--grn); }
.item.untranslated .marker { color: var(--yel); }
.editor { flex: 1; display: flex; flex-direction: column; }
.toolbar { padding: 8px 12px; background: var(--bg2); border-bottom: 1px solid var(--brd);
           display: flex; align-items: center; gap: 12px; font-size: 12px; }

/* Save-status badge. Pill with a colored dot + text. */
.savebadge { display: inline-flex; align-items: center; gap: 6px;
             padding: 3px 10px; border-radius: 999px; font-size: 11px;
             border: 1px solid var(--brd); background: var(--bg);
             color: var(--fg2); transition: background-color .15s, color .15s; }
.savebadge .dot { width: 8px; height: 8px; border-radius: 50%;
                  background: var(--fg2); transition: background-color .15s; }
.savebadge.idle    { color: var(--fg2); }
.savebadge.idle .dot    { background: var(--fg2); }
.savebadge.dirty   { color: var(--yel); border-color: var(--yel); }
.savebadge.dirty .dot   { background: var(--yel);
                          animation: dirty-pulse 1s ease-in-out infinite; }
.savebadge.saving  { color: var(--bg3-fg, #66a8ff); border-color: #66a8ff;
                     background: rgba(102,168,255,.08); }
.savebadge.saving .dot  { background: #66a8ff;
                          animation: saving-spin .8s linear infinite; }
.savebadge.saved   { color: var(--grn); border-color: var(--grn);
                     background: rgba(78,204,163,.08); }
.savebadge.saved .dot   { background: var(--grn); }
.savebadge.saved .check { color: var(--grn); font-weight: 700; }
.savebadge.error   { color: var(--acc); border-color: var(--acc);
                     background: rgba(233,69,96,.08); }
.savebadge.error .dot   { background: var(--acc); }

@keyframes dirty-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.35; }
}
@keyframes saving-spin {
  /* simple "breathing" pulse — easier to read than a CSS-only spinner */
  0%, 100% { transform: scale(1);   opacity: 1; }
  50%      { transform: scale(1.4); opacity: 0.6; }
}
.panes { flex: 1; display: grid; grid-template-rows: minmax(160px, 50vh) 1fr;
         overflow: hidden; }
.panes.no-preview { grid-template-rows: 1fr; }
.panes.no-preview .preview { display: none; }
.preview { padding: 16px; background: #050511; border-bottom: 1px solid var(--brd);
           display: flex; gap: 16px; align-items: flex-start;
           overflow-y: auto; overflow-x: auto; }
.preview img { display: block; }
.preview img { image-rendering: pixelated; image-rendering: crisp-edges; }
.text-area { flex: 1; display: grid; grid-template-columns: 1fr 1fr;
             gap: 8px; padding: 12px; overflow: hidden; }
.text-area.no-reference { grid-template-columns: 1fr; }
.text-area.no-reference .text-col.reference { display: none; }
.text-col { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.text-col label { font-size: 11px; color: var(--fg2); text-transform: uppercase;
                  letter-spacing: .5px; display: flex; align-items: center; gap: 6px; }
.text-col label .ro { font-size: 10px; padding: 1px 6px; border-radius: 3px;
                      background: var(--bg3); color: var(--fg2); border: 1px solid var(--brd); }
textarea { flex: 1; font-family: var(--code); font-size: 13px; line-height: 1.5;
           background: var(--bg2); color: var(--fg); border: 1px solid var(--brd);
           border-radius: 4px; padding: 10px; resize: none; outline: none;
           min-width: 0; }
textarea:focus { border-color: var(--acc); }
textarea[readonly] { background: var(--bg); color: var(--fg2); cursor: default; }

/* Width-overflow indication: a line wider than the file's cols_per_line
   would run off the screen in-game (no auto-wrap in most dialog
   renderers) — paint the script block red. */
textarea.overflow, textarea.overflow:focus {
  border-color: #ff3344; box-shadow: 0 0 0 1px #ff3344,
                                     0 0 8px rgba(255,51,68,.35); }
.ovf-badge { display: none; color: #ff3344; font-size: 10px;
             font-family: var(--code); font-weight: 700; margin-left: 6px; }
.ovf-badge.on { display: inline; }
.item .ovf { color: #ff3344; font-weight: 700; font-size: 10px;
             letter-spacing: .5px; }
.item.overflow { border-left: 3px solid #ff3344; padding-left: 9px;
                 background: rgba(255,51,68,.07); }
.item.overflow.active { border-left-color: #ff3344; }

/* Find/replace bar — slides down from the toolbar. */
.findbar { display: none; padding: 8px 12px; background: var(--bg2);
           border-bottom: 1px solid var(--brd);
           grid-template-columns: 1fr 1fr auto auto auto auto auto;
           gap: 6px; align-items: center; font-size: 12px; }
.findbar.open { display: grid; }
.findbar input[type=text] { background: var(--bg); color: var(--fg);
                             border: 1px solid var(--brd); border-radius: 3px;
                             padding: 4px 8px; font-family: var(--code); font-size: 12px;
                             outline: none; }
.findbar input[type=text]:focus { border-color: var(--acc); }
.findbar button { background: var(--bg3); color: var(--fg); border: 1px solid var(--brd);
                  border-radius: 3px; padding: 4px 9px; font-size: 11px; cursor: pointer; }
.findbar button:hover { background: var(--acc2); }
.findbar button.act { background: var(--acc); border-color: var(--acc); color: #fff; }
.findbar .count { color: var(--fg2); font-size: 11px; padding: 0 4px; }

/* Settings modal (folder paths). */
.modal-bg { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.6);
            z-index: 100; align-items: center; justify-content: center; }
.modal-bg.open { display: flex; }
.modal { background: var(--bg2); border: 1px solid var(--brd); border-radius: 6px;
         padding: 18px; min-width: 460px; max-width: 600px; }
.modal h3 { color: var(--acc); font-size: 13px; margin-bottom: 12px; }
.modal .row { margin-bottom: 10px; }
.modal .row label { display: block; font-size: 11px; color: var(--fg2);
                    text-transform: uppercase; margin-bottom: 4px; }
.modal .row input { width: 100%; padding: 6px 9px; background: var(--bg); color: var(--fg);
                    border: 1px solid var(--brd); border-radius: 3px; font-family: var(--code);
                    font-size: 12px; outline: none; }
.modal .row .meta { color: var(--fg2); font-size: 10px; margin-top: 3px; }
.modal .actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
.modal .actions button { padding: 5px 14px; border-radius: 3px; cursor: pointer;
                         border: 1px solid var(--brd); background: var(--bg3); color: var(--fg);
                         font-size: 12px; }
.modal .actions button.primary { background: var(--acc); border-color: var(--acc); color: #fff; }

/* Cross-scenario search results panel. */
.search-results { display: none; position: absolute; top: 50px; right: 16px;
                  width: 480px; max-height: 60vh; overflow-y: auto;
                  background: var(--bg2); border: 1px solid var(--brd); border-radius: 4px;
                  z-index: 50; padding: 6px; box-shadow: 0 6px 24px rgba(0,0,0,.5); }
.search-results.open { display: block; }
.search-results .hit { padding: 5px 8px; cursor: pointer; border-bottom: 1px solid var(--brd);
                       font-size: 12px; }
.search-results .hit:hover { background: var(--bg3); }
.search-results .hit .where { color: var(--acc); font-family: var(--code); font-size: 10px; }
.search-results .hit .ctx { color: var(--fg2); font-family: var(--code); font-size: 11px;
                            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.search-results .hit .ctx b { color: var(--yel); background: rgba(240,192,64,.15); padding: 0 2px; }
.search-results .empty { padding: 10px; color: var(--fg2); font-size: 12px; text-align: center; }
.hint { font-size: 11px; color: var(--fg2); line-height: 1.5; }
.hint code { background: var(--bg2); padding: 1px 5px; border-radius: 3px;
             color: var(--fg); font-family: var(--code); font-size: 11px; }
::-webkit-scrollbar { width: 7px; height: 7px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--brd); border-radius: 4px; }
.empty { padding: 24px; color: var(--fg2); font-size: 13px; text-align: center; }
</style></head><body>
  <div class="col"><h2>Files</h2><div class="list" id="scen-list"></div></div>
  <div class="col"><h2 id="ent-title">Entries</h2><div class="list" id="ent-list"></div></div>
  <div class="col editor">
    <div class="toolbar">
      <span id="ent-label">No entry selected</span>
      <span class="savebadge idle" id="savebadge" title="Save status">
        <span class="dot"></span>
        <span class="label" id="savebadge-label">idle</span>
      </span>
      <span style="flex:1"></span>
      <button onclick="toggleFindBar()" title="Find / replace (Ctrl+F)"
              style="background:var(--bg3);color:var(--fg);border:1px solid var(--brd);
                     border-radius:3px;padding:3px 9px;font-size:11px;cursor:pointer;">
        🔍 Find
      </button>
      <button onclick="openSettings()" title="Folder settings"
              style="background:var(--bg3);color:var(--fg);border:1px solid var(--brd);
                     border-radius:3px;padding:3px 9px;font-size:11px;cursor:pointer;">
        ⚙ Settings
      </button>
    </div>
    <div class="findbar" id="findbar">
      <input id="find-q" type="text" placeholder="Find…"
             onkeydown="if(event.key==='Enter'){event.shiftKey?findPrev():findNext()}; if(event.key==='Escape')toggleFindBar()">
      <input id="find-r" type="text" placeholder="Replace with…">
      <button id="find-case" onclick="toggleFindFlag('case', this)" title="Case-sensitive (Aa)">Aa</button>
      <button id="find-regex" onclick="toggleFindFlag('regex', this)" title="Regex">.*</button>
      <button onclick="findPrev()" title="Find previous (Shift+Enter)">◀</button>
      <button onclick="findNext()" title="Find next (Enter)">▶</button>
      <button onclick="replaceOne()" title="Replace one">Replace</button>
      <span class="count" id="find-count"></span>
    </div>
    <div class="search-results" id="search-results"></div>
    <div class="panes" id="panes" style="display:none">
      <div class="preview">
        <img id="preview-img" alt="preview" />
      </div>
      <div class="text-area" id="text-area">
        <div class="text-col">
          <label><span id="lbl-editable">EN</span> BODY
            <span style="color:var(--grn);font-size:9px;">editable</span>
            <span id="body-bytes" style="color:var(--fg2);font-size:10px;
                                         margin-left:6px;font-family:var(--code);"
                  title="encoded byte length (what the build will write)"></span>
            <span id="body-overflow" class="ovf-badge"></span></label>
          <textarea id="body" spellcheck="false" placeholder="select an entry"></textarea>
        </div>
        <div class="text-col reference">
          <label><span id="lbl-reference">JP</span> REFERENCE
            <span class="ro">read-only</span>
            <span id="jp-body-bytes" style="color:var(--fg2);font-size:10px;
                                            margin-left:6px;font-family:var(--code);"
                  title="encoded byte length of the reference entry"></span></label>
          <textarea id="jp-body" readonly spellcheck="false"
                    placeholder="reference source not loaded"></textarea>
        </div>
      </div>
    </div>
    <div class="empty" id="empty">Pick a file → entry to start editing.</div>
  </div>

  <!-- Settings modal -->
  <div class="modal-bg" id="settings-modal">
    <div class="modal">
      <h3>Script folder settings</h3>
      <div class="row">
        <label>Editable folder (label: <span id="cfg-en-label">EN</span>)</label>
        <div style="display:flex;gap:6px;">
          <input id="cfg-en" type="text" placeholder="/path/to/data/en" style="flex:1">
          <button onclick="browseFolder('en')"
                  style="padding:6px 12px;background:var(--bg3);color:var(--fg);
                         border:1px solid var(--brd);border-radius:3px;
                         font-size:11px;cursor:pointer;">📁 Browse</button>
        </div>
        <div class="meta" id="cfg-en-meta"></div>
      </div>
      <div class="row">
        <label>Reference folder (label: <span id="cfg-jp-label">JP</span>, read-only)</label>
        <div style="display:flex;gap:6px;">
          <input id="cfg-jp" type="text" placeholder="/path/to/data/jp" style="flex:1">
          <button onclick="browseFolder('jp')"
                  style="padding:6px 12px;background:var(--bg3);color:var(--fg);
                         border:1px solid var(--brd);border-radius:3px;
                         font-size:11px;cursor:pointer;">📁 Browse</button>
        </div>
        <div class="meta" id="cfg-jp-meta"></div>
      </div>
      <div class="meta" style="margin-top:8px;color:var(--fg2);font-size:10px;">
        Labels in the editor (e.g. <span id="cfg-en-label2">EN</span> BODY /
        <span id="cfg-jp-label2">JP</span> REFERENCE) are derived from each
        folder's basename, uppercased. Use folder names like
        <code style="background:var(--bg);padding:1px 4px;border-radius:2px;">data/fr/</code>
        for a French translation, etc.
      </div>
      <div class="actions">
        <button onclick="closeSettings()">Cancel</button>
        <button class="primary" onclick="saveSettings()">Save &amp; reload</button>
      </div>
    </div>
  </div>

<script>
let CURRENT = { scenario: null, entry_i: null };
let DEBOUNCE = null;
let PREVIEW_ENABLED = true;

function setPreviewPng(png) {
  // png is a data URI, or null in text-only mode (no font configured).
  const img = document.getElementById('preview-img');
  const panes = document.getElementById('panes');
  if (png) {
    img.src = png;
    panes.classList.remove('no-preview');
  } else {
    img.removeAttribute('src');
    panes.classList.add('no-preview');
  }
}

async function loadScenarios() {
  // Apply current folder labels first so the textarea headers match the
  // active folder pair from the very first frame.
  await applyLabelsAtStartup();
  const items = await pywebview.api.list_scenarios();
  const list = document.getElementById('scen-list');
  list.innerHTML = '';
  items.forEach(it => {
    const div = document.createElement('div');
    div.className = 'item';
    div.dataset.name = it.name;
    const pct = it.entries ? Math.round(100 * it.translated / it.entries) : 0;
    const ovf = it.overflow
      ? ` <span class="ovf" title="${it.overflow} entr${it.overflow === 1 ? 'y' : 'ies'} with text wider than the screen">⚠ ${it.overflow}</span>`
      : '';
    div.innerHTML = `<div>${it.name.replace('scenario_', 'scen ')}${ovf}</div>
                     <div class="meta">${it.translated}/${it.entries} translated · ${pct}%</div>`;
    div.onclick = () => selectScenario(it.name);
    list.appendChild(div);
  });
  // After listing, restore the previous session if any.
  const st = await pywebview.api.load_session_state();
  if (st && st.scenario) {
    await selectScenario(st.scenario);
    if (typeof st.entry_i === 'number') {
      await selectEntry(st.entry_i);
      // Defer cursor/scroll restore until after the textarea is populated
      // by selectEntry (which awaits get_entry).
      const ta = document.getElementById('body');
      if (typeof st.cursor === 'number') {
        try { ta.setSelectionRange(st.cursor, st.cursor); } catch (e) {}
      }
      if (typeof st.scroll === 'number') {
        ta.scrollTop = st.scroll;
      }
      // Scroll the entry into view in the middle pane.
      const item = document.querySelector(`#ent-list .item[data-i="${st.entry_i}"]`);
      if (item) item.scrollIntoView({ block: 'center', behavior: 'instant' });
    }
  }
}

// Debounced session-state save — fires on cursor/scroll/edit changes.
let SESSION_SAVE_TIMER = null;
function bumpSessionSave() {
  if (CURRENT.scenario === null || CURRENT.entry_i === null) return;
  if (SESSION_SAVE_TIMER) clearTimeout(SESSION_SAVE_TIMER);
  SESSION_SAVE_TIMER = setTimeout(() => {
    const ta = document.getElementById('body');
    pywebview.api.save_session_state(
      CURRENT.scenario, CURRENT.entry_i,
      ta.selectionStart || 0,
      ta.scrollTop || 0,
    );
  }, 500);
}

async function selectScenario(name) {
  document.querySelectorAll('#scen-list .item').forEach(d => {
    d.classList.toggle('active', d.dataset.name === name);
  });
  CURRENT.scenario = name;
  CURRENT.entry_i = null;
  document.getElementById('ent-title').textContent = name.replace('scenario_', 'Scen ');
  const entries = await pywebview.api.list_entries(name);
  const list = document.getElementById('ent-list');
  list.innerHTML = '';
  entries.forEach(e => {
    const div = document.createElement('div');
    div.className = 'item ' + (e.translated ? 'translated' : 'untranslated');
    div.dataset.i = e.i;
    const marker = e.translated ? '●' : '○';
    const ovf = e.overflow
      ? ' <span class="ovf" title="a line is wider than the allotted width — text runs off-screen in-game">⚠ OVERFLOW</span>'
      : '';
    if (e.overflow) div.classList.add('overflow');
    div.innerHTML = `<div><span class="marker">${marker}</span> #${e.idx}${ovf}</div>
                     <div class="meta">${escapeHtml(e.preview) || '(empty)'}</div>`;
    div.onclick = () => selectEntry(e.i);
    list.appendChild(div);
  });
  document.getElementById('panes').style.display = 'none';
  document.getElementById('empty').style.display = 'block';
  document.getElementById('empty').textContent = `Pick an entry (${entries.length} in ${name}).`;
}

async function selectEntry(i) {
  document.querySelectorAll('#ent-list .item').forEach(d => {
    d.classList.toggle('active', Number(d.dataset.i) === i);
  });
  CURRENT.entry_i = i;
  const e = await pywebview.api.get_entry(CURRENT.scenario, i);
  document.getElementById('body').value = e.body;
  document.getElementById('jp-body').value = e.jp_body || '';
  document.getElementById('text-area').classList.toggle('no-reference', !e.jp_body);
  setPreviewPng(e.preview_png);
  setBodyBytes(e.byte_count);
  setJpBodyBytes(e.jp_byte_count);
  setOverflow(e.overflow);
  document.getElementById('ent-label').textContent =
    `${CURRENT.scenario} · entry #${i}`;
  // Loading a new entry resets the badge to idle — any pending save from
  // the previous entry has already been queued via queue_save.
  if (SAVED_FADE_TIMER) { clearTimeout(SAVED_FADE_TIMER); SAVED_FADE_TIMER = null; }
  setBadge('idle', 'idle');
  document.getElementById('empty').style.display = 'none';
  document.getElementById('panes').style.display = 'grid';
  bumpSessionSave();
}

// Save-state machine:
//   user types         → setBadge('dirty', 'editing…')
//   debounce fires     → setBadge('saving', 'saving…')
//   bridge ack (poll)  → setBadge('saved', 'saved ✓')  (3s auto-fade to idle)
//   bridge error       → setBadge('error', 'save failed')
let SAVE_POLL = null;
let LAST_SEEN_SEQ = 0;
let SAVED_FADE_TIMER = null;

function setBadge(cls, text) {
  const el = document.getElementById('savebadge');
  el.className = 'savebadge ' + cls;
  document.getElementById('savebadge-label').textContent = text;
}

function setBodyBytes(n) {
  // Encoded byte length next to the BODY label. Hidden when null/undefined
  // (e.g. before any entry is loaded).
  const el = document.getElementById('body-bytes');
  if (!el) return;
  if (n == null) { el.textContent = ''; return; }
  el.textContent = n + ' B';
}

function markEntryOverflow(entryI, over) {
  // Toggle the ⚠ marker + red edge on one entry-list row in place (used by
  // the live-edit path so the list stays truthful without a full reload).
  const item = document.querySelector(`#ent-list .item[data-i="${entryI}"]`);
  if (!item) return;
  item.classList.toggle('overflow', over);
  const head = item.querySelector('div');
  let badge = item.querySelector('.ovf');
  if (over && !badge) {
    badge = document.createElement('span');
    badge.className = 'ovf';
    badge.title = 'a line is wider than the allotted width — text runs off-screen in-game';
    badge.textContent = ' ⚠ OVERFLOW';
    head.appendChild(badge);
  } else if (!over && badge) {
    badge.remove();
  }
}

function setOverflow(o) {
  // Red indication when any explicit line is wider than the file's
  // allotted width (in-game the line would run off the screen). `o` is
  // {limit, lines:[{line, units}]} from the bridge; null/empty clears it.
  const badge = document.getElementById('body-overflow');
  const ta = document.getElementById('body');
  const over = o && o.lines && o.lines.length;
  ta.classList.toggle('overflow', !!over);
  badge.classList.toggle('on', !!over);
  if (!over) { badge.textContent = ''; badge.title = ''; return; }
  const nums = o.lines.map(l => l.line).join(', ');
  badge.textContent = `⚠ line ${nums} over ${o.limit}-col width`;
  badge.title = o.lines
    .map(l => `line ${l.line}: ${l.units}/${o.limit} cols — runs off-screen in-game`)
    .join('\n');
}

function setJpBodyBytes(n) {
  // Encoded byte length of the reference entry — read-only counterpart.
  const el = document.getElementById('jp-body-bytes');
  if (!el) return;
  if (n == null || n === 0) { el.textContent = ''; return; }
  el.textContent = n + ' B';
}

function startSavePolling() {
  if (SAVE_POLL) return;
  SAVE_POLL = setInterval(async () => {
    try {
      const s = await pywebview.api.get_save_state();
      if (s.state === 'queued' || s.state === 'saving') {
        setBadge('saving', 'saving…');
      } else if (s.state === 'saved' && s.seq !== LAST_SEEN_SEQ) {
        LAST_SEEN_SEQ = s.seq;
        setBadge('saved', 'saved ✓');
        clearInterval(SAVE_POLL); SAVE_POLL = null;
        if (SAVED_FADE_TIMER) clearTimeout(SAVED_FADE_TIMER);
        SAVED_FADE_TIMER = setTimeout(() => setBadge('idle', 'idle'), 3000);
      } else if (s.state === 'error') {
        setBadge('error', 'save failed: ' + (s.error || 'unknown'));
        clearInterval(SAVE_POLL); SAVE_POLL = null;
      }
    } catch (e) { /* swallow — keep polling */ }
  }, 120);
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

document.getElementById('body').addEventListener('input', () => {
  if (CURRENT.entry_i === null) return;
  const body = document.getElementById('body').value;
  setBadge('dirty', 'editing…');
  bumpSessionSave();
  if (DEBOUNCE) clearTimeout(DEBOUNCE);
  DEBOUNCE = setTimeout(async () => {
    try {
      const r = await pywebview.api.render_body(body, CURRENT.scenario);
      setPreviewPng(r.png);
      setBodyBytes(r.byte_count);
      setOverflow(r.overflow);
      // Keep the entry list's ⚠ in sync as the user types.
      markEntryOverflow(CURRENT.entry_i,
                        !!(r.overflow && r.overflow.lines.length));
      await pywebview.api.queue_save(CURRENT.scenario, CURRENT.entry_i, body);
      // queue_save returns immediately; the file write happens ~400ms later
      // on a Python Timer. Poll get_save_state() to know when it lands.
      startSavePolling();
    } catch (err) {
      setBadge('error', 'save failed: ' + err);
    }
  }, 200);
});

// Cursor & scroll movements within the textarea also update session state.
document.getElementById('body').addEventListener('keyup', bumpSessionSave);
document.getElementById('body').addEventListener('click', bumpSessionSave);
document.getElementById('body').addEventListener('scroll', bumpSessionSave);

document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    setBadge('saving', 'saving (manual)…');
    pywebview.api.flush_now();
    startSavePolling();
  }
});

window.addEventListener('beforeunload', () => {
  pywebview.api.flush_now();
});

// ---- Find / replace ----
const FIND_FLAGS = { case: false, regex: false };
let FIND_RESULTS = [];     // [{scenario, entry_i, start, end, ...}]
let FIND_INDEX = -1;       // current selection in FIND_RESULTS

function toggleFindBar() {
  const bar = document.getElementById('findbar');
  bar.classList.toggle('open');
  if (bar.classList.contains('open')) document.getElementById('find-q').focus();
  else { hideSearchResults(); }
}
function toggleFindFlag(name, btn) {
  FIND_FLAGS[name] = !FIND_FLAGS[name];
  btn.classList.toggle('act', FIND_FLAGS[name]);
  runSearch();  // rerun if query non-empty
}

function showSearchResults(results, truncated) {
  const panel = document.getElementById('search-results');
  panel.innerHTML = '';
  if (!results.length) {
    panel.innerHTML = '<div class="empty">no matches</div>';
    panel.classList.add('open');
    return;
  }
  results.slice(0, 100).forEach((r, idx) => {
    const div = document.createElement('div');
    div.className = 'hit';
    div.innerHTML = `
      <div class="where">${r.scenario} · #${r.entry_i}</div>
      <div class="ctx">…${escapeHtml(r.before)}<b>${escapeHtml(r.match)}</b>${escapeHtml(r.after)}…</div>`;
    div.onclick = () => jumpToHit(idx);
    panel.appendChild(div);
  });
  if (truncated) {
    const note = document.createElement('div');
    note.className = 'empty';
    note.textContent = '(more matches not shown; refine query)';
    panel.appendChild(note);
  }
  panel.classList.add('open');
}
function hideSearchResults() {
  document.getElementById('search-results').classList.remove('open');
}

async function runSearch() {
  const q = document.getElementById('find-q').value;
  if (!q) { hideSearchResults(); FIND_RESULTS = []; FIND_INDEX = -1; updateFindCount(); return; }
  const res = await pywebview.api.search_text(
    q, FIND_FLAGS.case, FIND_FLAGS.regex,
    'all', 'en',
  );
  if (res.error) {
    document.getElementById('find-count').textContent = 'regex err';
    return;
  }
  FIND_RESULTS = res.matches;
  FIND_INDEX = -1;
  showSearchResults(res.matches, res.truncated);
  updateFindCount();
}

function updateFindCount() {
  const el = document.getElementById('find-count');
  if (!FIND_RESULTS.length) { el.textContent = ''; return; }
  el.textContent = `${FIND_INDEX < 0 ? 0 : FIND_INDEX + 1} of ${FIND_RESULTS.length}`;
}

async function jumpToHit(idx) {
  if (idx < 0 || idx >= FIND_RESULTS.length) return;
  const hit = FIND_RESULTS[idx];
  FIND_INDEX = idx;
  if (CURRENT.scenario !== hit.scenario) {
    await selectScenario(hit.scenario);
  }
  await selectEntry(hit.entry_i);
  const ta = document.getElementById('body');
  ta.focus();
  ta.setSelectionRange(hit.start, hit.end);
  // Scroll the selection into view
  const lineHeight = parseFloat(getComputedStyle(ta).lineHeight) || 18;
  const before = ta.value.slice(0, hit.start);
  const lineNum = (before.match(/\n/g) || []).length;
  ta.scrollTop = Math.max(0, lineNum * lineHeight - 80);
  // Dismiss the results panel once we've jumped — the user wants the
  // textarea visible. They can re-open by editing the query.
  hideSearchResults();
  updateFindCount();
}

// Click-outside dismiss for the search-results panel. Listen on
// `mousedown` (not `click`) so we close BEFORE any text selection
// happens, which feels snappier.
document.addEventListener('mousedown', (e) => {
  const panel = document.getElementById('search-results');
  if (!panel.classList.contains('open')) return;
  // Keep the panel open if the click is inside it, inside the find-bar
  // (the find-bar drives it), or on the Find toolbar button itself.
  if (panel.contains(e.target)) return;
  if (document.getElementById('findbar').contains(e.target)) return;
  // The Find button has no id; match by its label text.
  if (e.target.closest('button') && e.target.closest('button').textContent.includes('Find')) return;
  hideSearchResults();
});

async function findNext() {
  if (!FIND_RESULTS.length) { await runSearch(); }
  if (!FIND_RESULTS.length) return;
  jumpToHit((FIND_INDEX + 1) % FIND_RESULTS.length);
}
async function findPrev() {
  if (!FIND_RESULTS.length) { await runSearch(); }
  if (!FIND_RESULTS.length) return;
  jumpToHit((FIND_INDEX - 1 + FIND_RESULTS.length) % FIND_RESULTS.length);
}

async function replaceOne() {
  if (FIND_INDEX < 0 || !FIND_RESULTS.length) {
    await findNext();
    return;
  }
  const hit = FIND_RESULTS[FIND_INDEX];
  const replacement = document.getElementById('find-r').value;
  await pywebview.api.replace_text(hit.scenario, hit.entry_i, hit.start, hit.end, replacement);
  // Refresh the entry in the editor — body changed
  if (CURRENT.scenario === hit.scenario && CURRENT.entry_i === hit.entry_i) {
    const e = await pywebview.api.get_entry(hit.scenario, hit.entry_i);
    document.getElementById('body').value = e.body;
    setPreviewPng(e.preview_png);
    setBodyBytes(e.byte_count);
    setJpBodyBytes(e.jp_byte_count);
  }
  // Re-run the search (offsets shifted after replacement)
  await runSearch();
}

// Debounced live search as user types in the query field
let SEARCH_DEBOUNCE = null;
document.getElementById('find-q').addEventListener('input', () => {
  if (SEARCH_DEBOUNCE) clearTimeout(SEARCH_DEBOUNCE);
  SEARCH_DEBOUNCE = setTimeout(runSearch, 200);
});

document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
    e.preventDefault();
    if (!document.getElementById('findbar').classList.contains('open')) toggleFindBar();
    else document.getElementById('find-q').focus();
  }
});

// ---- Settings modal ----
function applyLabels(s) {
  // Drive every "EN" / "JP" UI label from the folder basename so the editor
  // generalizes to FR / ES / DE / KO / etc. without code changes.
  if (s.title) document.title = s.title;
  PREVIEW_ENABLED = !!s.preview_enabled;
  if (!PREVIEW_ENABLED) document.getElementById('panes').classList.add('no-preview');
  document.getElementById('lbl-editable').textContent = s.editable_label;
  document.getElementById('lbl-reference').textContent = s.reference_label;
  // Mirror in the settings modal too.
  document.getElementById('cfg-en-label').textContent = s.editable_label;
  document.getElementById('cfg-jp-label').textContent = s.reference_label;
  const l2a = document.getElementById('cfg-en-label2');
  const l2b = document.getElementById('cfg-jp-label2');
  if (l2a) l2a.textContent = s.editable_label;
  if (l2b) l2b.textContent = s.reference_label;
}

async function openSettings() {
  const s = await pywebview.api.get_settings();
  document.getElementById('cfg-en').value = s.en_dir;
  document.getElementById('cfg-jp').value = s.jp_dir;
  document.getElementById('cfg-en-meta').textContent =
    s.en_dir_exists ? `✓ exists  (default: ${s.default_en_dir})`
                    : `⚠ folder not found  (default: ${s.default_en_dir})`;
  document.getElementById('cfg-jp-meta').textContent =
    s.jp_dir_exists ? `✓ exists  (default: ${s.default_jp_dir})`
                    : `⚠ folder not found  (default: ${s.default_jp_dir})`;
  applyLabels(s);
  document.getElementById('settings-modal').classList.add('open');
}
function closeSettings() { document.getElementById('settings-modal').classList.remove('open'); }

async function browseFolder(which) {
  const inputId = which === 'en' ? 'cfg-en' : 'cfg-jp';
  const current = document.getElementById(inputId).value.trim();
  const chosen = await pywebview.api.pick_folder(current);
  if (chosen) {
    document.getElementById(inputId).value = chosen;
    // Update label preview live as the user picks
    const tmp = chosen.replace(/\/+$/, '').split('/').pop().toUpperCase().slice(0, 8) || '—';
    document.getElementById(which === 'en' ? 'cfg-en-label' : 'cfg-jp-label').textContent = tmp;
    const l2 = document.getElementById(which === 'en' ? 'cfg-en-label2' : 'cfg-jp-label2');
    if (l2) l2.textContent = tmp;
  }
}

async function saveSettings() {
  const en = document.getElementById('cfg-en').value.trim();
  const jp = document.getElementById('cfg-jp').value.trim();
  const s = await pywebview.api.set_settings(en, jp);
  applyLabels(s);
  closeSettings();
  // Reload scenarios from new paths
  await loadScenarios();
}

// On launch, apply labels once so the editor textareas show the right
// pair (e.g. EN/JP, FR/JP, etc.) before any settings change.
async function applyLabelsAtStartup() {
  try {
    const s = await pywebview.api.get_settings();
    applyLabels(s);
  } catch (e) { /* pywebview not ready yet — applyLabels will fire from loadScenarios */ }
}

window.addEventListener('pywebviewready', loadScenarios);
</script></body></html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_editor(project_root: Path | str = ".") -> int:
    """Load the project's editor config and launch the UI. Returns an exit
    code (0 ok, nonzero on a startup problem)."""
    try:
        config = load_editor_config(project_root)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not config.data_dir.is_dir():
        print(f"error: script folder not found: {config.data_dir}\n"
              f"Set [editor].data_dir (or a {config.lang}_data_dir scalar) "
              f"in {config.root / 'project.toml'}.", file=sys.stderr)
        return 2

    try:
        import webview
    except ImportError:
        print("error: pywebview not installed — "
              "pip install 'retrotool[editor]'", file=sys.stderr)
        return 2

    bridge = Bridge(config)
    if not bridge.scenarios:
        pats = ", ".join(config.file_patterns)
        print(f"warning: no script files matching [{pats}] in "
              f"{bridge.data_dir} — check [editor].file_patterns.",
              file=sys.stderr)
    print(f"project: {config.project_name} ({config.root})")
    print(f"editing: {bridge.data_dir}  (table: "
          f"{config.table.name if config.table else 'none — bytes approximate'})")
    print(f"preview: {'on' if bridge.assets.enabled else 'off (text-only; set [editor.preview] font)'}")
    print(f"loaded {len(bridge.scenarios)} files, "
          f"{sum(len(s.entries) for s in bridge.scenarios.values())} entries")

    # Write HTML to temp file (pywebview handles file:// URLs better than inline)
    tmp = tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8"
    )
    tmp.write(HTML)
    tmp.close()

    webview.create_window(
        f"{config.project_name} — Script Editor",
        url=f"file://{tmp.name}",
        js_api=bridge,
        width=1400,
        height=900,
        min_size=(900, 600),
    )
    try:
        webview.start(debug=False)
    finally:
        bridge.flush_now()  # final flush on exit
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="retrotool edit",
        description="GUI script editor for retrotool translation projects",
    )
    parser.add_argument(
        "project", nargs="?", default=".",
        help="project directory containing project.toml (default: .)",
    )
    args = parser.parse_args(argv)
    return run_editor(args.project)


if __name__ == "__main__":
    sys.exit(main())

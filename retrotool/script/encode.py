"""Script text encoder ported from sfc-lm3-eng/lm3.py.

Two public entry points:

  - `encode_text(text, table, fallback_table=None)` → `(bytes, fixups, labels)`
    Longest-match encode with primary + fallback table. Honors `[HH..]` hex
    literals, `{HH}` raw bytecodes, `[FFC0@N[:label]]` entry-reference fixups,
    and `[label:NAME]` zero-width offset markers.

  - `encode_script_file(path, table_path, *, fallback_table=None, word_wrap=None,
    sub_table_filter=None, textbuf_limit=None)` → list of
    `(encoded_bytes, original_address, fixups, entry_labels)` per entry.
    Entries split on `<<HEADER>>` markers. Header form:
    `<<$TBLPTR:ENTRYIDX[$DATAPTR]>>`. Word-wrap applied per `entries` filter.

The format must remain byte-equivalent to LM3's encoder — pointer-table
handlers in `mbuild` rely on this for round-trip parity.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from retrotool.script.table import Table


_BRACKET_TOKEN_RE = re.compile(r'\[[^\]]*\]|\{[0-9A-Fa-f]{2}\}')


@dataclass(frozen=True)
class ScriptFixup:
    """One unresolved address inside an encoded entry.

    `offset` is the byte index (within the containing entry) where the 3-byte
    little-endian SNES pointer should be written. Exactly one of `entry_idx`
    (local entry-reference) or `global_label` (global-label registry) is set.
    `label` is an optional named offset inside the referenced entry.
    """
    offset: int
    entry_idx: Optional[int] = None
    label: Optional[str] = None
    global_label: Optional[str] = None


def _int_to_bytes_be(val: int) -> bytes:
    if val == 0:
        return b'\x00'
    out: list[int] = []
    v = val
    while v > 0:
        out.append(v & 0xFF)
        v >>= 8
    out.reverse()
    return bytes(out)


_LABEL_RE = re.compile(r'\[label:(\w+)\]')
# Generalized redirect/reference grammar:
#   [HHHH@N]          → opcode HHHH (2 bytes) + 3-byte placeholder, fixup→entry N
#   [HHHH@N:label]    → same, but resolves to a `[label:NAME]` inside entry N
#   [HHHH@@global]    → same, resolves against the global-label registry
# HHHH is any 4-hex-digit opcode. This covers FFC0, FFF7, and future
# control-prefix variants (Table.ctrl_prefix is configurable per-game).
_REDIRECT_REF_RE = re.compile(
    r'\[([0-9A-Fa-f]{4})@(?:@(\w+)|(\d+)(?::(\w+))?)\]'
)


def encode_text(
    text_str: str,
    table: Table,
    fallback_table: Optional[Table] = None,
) -> tuple[bytes, list[ScriptFixup], dict[str, int]]:
    """Encode text → bytes. Returns (encoded, fixups, labels).

    fixups: list of `ScriptFixup` records (entry refs or global-label refs).
    labels: dict of label_name → byte_offset within encoded (for [label:NAME]).
    """
    # Prefer `char_bytes` (preserves declared hex-code byte width). Fall
    # back to `char_map` + `_int_to_bytes_be` only if a table instance
    # predates the `char_bytes` addition.
    char_bytes = getattr(table, 'char_bytes', None) or {
        k: _int_to_bytes_be(v) for k, v in table.char_map.items()
    }
    fb_bytes: dict[str, bytes]
    if fallback_table is not None:
        fb_bytes = getattr(fallback_table, 'char_bytes', None) or {
            k: _int_to_bytes_be(v) for k, v in fallback_table.char_map.items()
        }
    else:
        fb_bytes = {}

    max_key_len = max((len(k) for k in char_bytes), default=1)
    fb_max_key_len = max((len(k) for k in fb_bytes), default=1) if fb_bytes else 1

    result = bytearray()
    fixups: list[ScriptFixup] = []
    labels: dict[str, int] = {}

    i = 0
    n = len(text_str)
    while i < n:
        ch = text_str[i]

        # Skip raw newlines
        if ch in '\n\r':
            i += 1
            continue

        # {XX} bytecode
        if ch == '{':
            close = text_str.find('}', i + 1)
            if close != -1:
                hex_str = text_str[i + 1:close]
                if len(hex_str) == 2 and all(c in '0123456789ABCDEFabcdef' for c in hex_str):
                    result.append(int(hex_str, 16))
                    i = close + 1
                    continue

        matched = False
        if ch == '[':
            # Multi-char primary table matches (length >= 2)
            for length in range(min(max_key_len, n - i), 1, -1):
                substr = text_str[i:i + length]
                raw = char_bytes.get(substr)
                if raw is not None:
                    result.extend(raw)
                    i += length
                    matched = True
                    break

            # [HHHH@N[:label]] entry-ref or [HHHH@@name] global-ref
            if not matched:
                m = _REDIRECT_REF_RE.match(text_str, i)
                if m:
                    opcode = bytes.fromhex(m.group(1))
                    global_name = m.group(2)
                    result.extend(opcode)
                    off = len(result)
                    if global_name is not None:
                        fixups.append(ScriptFixup(offset=off, global_label=global_name))
                    else:
                        fixups.append(ScriptFixup(
                            offset=off,
                            entry_idx=int(m.group(3)),
                            label=m.group(4),
                        ))
                    result.extend(b'\xFF\xFF\xFF')
                    i = m.end()
                    matched = True

            # [label:NAME]
            if not matched:
                m = _LABEL_RE.match(text_str, i)
                if m:
                    labels[m.group(1)] = len(result)
                    i = m.end()
                    matched = True

            # [HH..] hex escape
            if not matched:
                close = text_str.find(']', i + 1)
                if close != -1:
                    hex_str = text_str[i + 1:close]
                    if (len(hex_str) >= 2 and len(hex_str) % 2 == 0
                            and all(c in '0123456789ABCDEFabcdef' for c in hex_str)):
                        result.extend(bytes.fromhex(hex_str))
                        i = close + 1
                        matched = True

            # Fallback multi-char starting with '['
            if not matched and fb_bytes:
                for length in range(min(fb_max_key_len, n - i), 1, -1):
                    substr = text_str[i:i + length]
                    if not substr.startswith('['):
                        continue
                    raw = fb_bytes.get(substr)
                    if raw is not None:
                        result.extend(raw)
                        i += length
                        matched = True
                        break

            # Single '[' from primary
            if not matched:
                raw = char_bytes.get('[')
                if raw is not None:
                    result.extend(raw)
                    i += 1
                    matched = True
        else:
            combined_max = max(max_key_len, fb_max_key_len)
            for length in range(min(combined_max, n - i), 0, -1):
                substr = text_str[i:i + length]
                raw = char_bytes.get(substr)
                if raw is None and fb_bytes:
                    raw = fb_bytes.get(substr)
                if raw is not None:
                    result.extend(raw)
                    i += length
                    matched = True
                    break

        if matched:
            continue

        # Last resort: printable ASCII identity, else '?'
        if 0x20 <= ord(ch) <= 0x7E:
            result.append(ord(ch))
        else:
            result.append(0x3F)
        i += 1

    return bytes(result), fixups, labels


def word_wrap_text(
    text: str, line_width: int, max_lines: int,
    *, newline: str = '[nl]',
    wrap_mode: str = 'newline',
    fill_char: str = ' ',
) -> tuple[str, bool, int]:
    """Word-wrap with newline-token insertion. Returns (wrapped, was_truncated, num_lines).

    `newline` is the encoder token emitted at each soft wrap AND recognized in
    the source text as a manual break. Defaults to `[nl]`; override per-table
    via `[word_wrap] newline = "[foo]"` for games that use a different token.

    `wrap_mode`:
      * `"newline"` (default) — emit `newline` token at each soft wrap.
      * `"pad"` — pad each non-final line to exactly `line_width` columns with
        `fill_char` and emit NO newline tokens. Intended for text engines that
        auto-wrap at a fixed column (e.g. unit-info style panels) and render
        newline tokens as visible artifacts. Requires a single-char `fill_char`.

    Truncation past `max_lines` always drops (with trailing-hex preservation).
    The `was_truncated` return lets the caller force an overflow route so the
    redirect still fires even when truncated bytes happen to fit the slot.
    """
    if wrap_mode not in ('newline', 'pad'):
        raise ValueError(f"word_wrap wrap_mode must be 'newline' or 'pad', got {wrap_mode!r}")
    if wrap_mode == 'pad' and len(fill_char) != 1:
        raise ValueError(
            f"word_wrap fill_char must be a single character in pad mode, got {fill_char!r}"
        )
    normalized = text.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
    normalized = re.sub(r' {2,}', ' ', normalized).strip()

    tokens = re.findall(r'\[[^\]]*\]|\{[0-9A-Fa-f]{2}\}| +|[^ \[\{]+', normalized)

    lines: list[str] = []
    line_nl: list[bool] = []
    current_line = ''
    col = 0

    def _flush(explicit: bool) -> None:
        nonlocal current_line, col
        lines.append(current_line.rstrip(' '))
        line_nl.append(explicit)
        current_line = ''
        col = 0

    for token in tokens:
        if token == newline:
            _flush(True)
            continue
        if (token.startswith('[') and token.endswith(']')) or \
           (token.startswith('{') and token.endswith('}')):
            current_line += token
            continue
        if token.strip() == '':
            if 0 < col < line_width:
                current_line += ' '
                col += 1
            continue
        word_len = len(token)
        if col + word_len <= line_width:
            current_line += token
            col += word_len
            if col == line_width:
                _flush(False)
        elif word_len <= line_width:
            _flush(True)
            current_line = token
            col = word_len
        else:
            while token:
                remaining = line_width - col
                if remaining <= 0:
                    _flush(True)
                    remaining = line_width
                chunk = token[:remaining]
                current_line += chunk
                col += len(chunk)
                token = token[remaining:]
                if col == line_width and token:
                    _flush(False)

    if current_line:
        lines.append(current_line.rstrip(' '))
        line_nl.append(False)

    truncated = len(lines) > max_lines
    if truncated:
        dropped_text = ''.join(lines[max_lines:])
        # Preserve any bracket tokens from dropped content so control codes
        # (terminators, FFC0 redirects, raw hex, named codes like [end]) still
        # reach the encoded output. Without this, a truncated entry written to
        # an FFC0 target has no terminator and the text engine keeps reading
        # into neighboring target regions.
        trailing_tokens = re.findall(
            r'\[[^\]]*\]|\{[0-9A-Fa-f]{2}\}',
            dropped_text,
        )
        lines = lines[:max_lines]
        line_nl = line_nl[:max_lines]
        if trailing_tokens:
            lines[-1] += ''.join(trailing_tokens)

    parts: list[str] = []
    if wrap_mode == 'pad':
        # Pad every non-final line out to line_width with fill_char; emit no
        # newline tokens. Bracket/brace tokens are zero-col (consistent with
        # the measurement used during wrapping), so we measure the visible
        # column count the same way when computing how much to pad.
        for i, line in enumerate(lines):
            parts.append(line)
            if i < len(lines) - 1:
                visible = len(_BRACKET_TOKEN_RE.sub('', line))
                padding = line_width - visible
                if padding > 0:
                    parts.append(fill_char * padding)
    else:
        for i, line in enumerate(lines):
            parts.append(line)
            if i < len(lines) - 1 and line_nl[i]:
                parts.append(newline)
    return ''.join(parts), truncated, len(lines)


def entry_in_range(idx: int, entries_spec) -> bool:
    if entries_spec is None:
        return True
    if isinstance(entries_spec, (list, set)):
        return idx in entries_spec
    for part in str(entries_spec).split(','):
        part = part.strip()
        if '-' in part:
            lo, hi = part.split('-', 1)
            if int(lo) <= idx <= int(hi):
                return True
        elif part.isdigit():
            if idx == int(part):
                return True
    return False


def _read_script_text(path: Path) -> str:
    with open(path, 'rb') as f:
        bom = f.read(2)
    encoding = 'utf-16' if bom == b'\xff\xfe' else 'utf-8'
    with open(path, 'r', encoding=encoding) as f:
        return f.read()


def encode_script_file(
    script_file: Union[str, Path],
    table_filename: Union[str, Path],
    *,
    fallback_table: Optional[Union[str, Path]] = None,
    word_wrap: Optional[dict] = None,
    sub_table_filter: Optional[int] = None,
    textbuf_limit: Optional[int] = None,
) -> list[tuple[bytes, Optional[int], list[ScriptFixup], dict[str, int], bool]]:
    """Parse <<index>>-delimited script and encode each entry.

    Returns list of (encoded_bytes, original_address, fixups, labels, force_overflow).
    `force_overflow` is True when the entry's source was truncated by word_wrap
    — the handler should route through the overflow strategy even if the
    (truncated) encoded bytes happen to fit the inline slot, so the redirect
    still fires in parity with other oversized entries. Entries are emitted in
    header-index order; gaps fill with `b'\\x00'`.
    """
    tbl = Table(str(table_filename))
    fb_tbl = Table(str(fallback_table)) if fallback_table else None

    text = _read_script_text(Path(script_file))
    raw_entries = text.split('<<')[1:]

    parsed: dict[int, tuple[str, Optional[int]]] = {}
    file_order: list[int] = []
    for entry in raw_entries:
        if '>>' not in entry:
            continue
        header = entry.split('>>')[0]
        if not header.startswith('$'):
            continue
        content = '>>'.join(entry.split('>>')[1:])
        if content.startswith('\n'):
            content = content[1:]
        content = content.rstrip('\n\r\t ')

        orig_addr: Optional[int] = None
        addr_match = re.search(r'\[\$(\d+)\]', header)
        if addr_match:
            orig_addr = int(addr_match.group(1))
        tbl_match = re.match(r'\$(\d+):', header)
        tbl_addr = int(tbl_match.group(1)) if tbl_match else None
        idx_match = re.search(r':(\d+)', header)
        header_idx = int(idx_match.group(1)) if idx_match else len(file_order)

        if sub_table_filter is not None and tbl_addr is not None and tbl_addr != sub_table_filter:
            continue
        parsed[header_idx] = (content, orig_addr)
        file_order.append(header_idx)

    encoded_entries: list[tuple[bytes, Optional[int], list, dict, bool]] = []
    if not parsed:
        return encoded_entries

    max_idx = max(parsed)
    for entry_idx in range(max_idx + 1):
        if entry_idx not in parsed:
            encoded_entries.append((b'\x00', None, [], {}, False))
            continue
        content, orig_addr = parsed[entry_idx]
        if not content or content == '[end]':
            encoded_entries.append((b'\x00', orig_addr, [], {}, False))
            continue
        if '<<<window' in content:
            # Windowed entries handled by separate path; keep slot.
            encoded_entries.append((b'\x00', orig_addr, [], {}, False))
            continue
        force_overflow = False
        if word_wrap is not None and entry_in_range(entry_idx, word_wrap.get('entries')):
            content, was_truncated, _ = word_wrap_text(
                content, word_wrap['line_width'], word_wrap['max_lines'],
                newline=word_wrap.get('newline', '[nl]'),
                wrap_mode=word_wrap.get('wrap_mode', 'newline'),
                fill_char=word_wrap.get('fill_char', ' '),
            )
            force_overflow = was_truncated
        encoded, fixups, labels = encode_text(content, tbl, fallback_table=fb_tbl)
        encoded_entries.append((encoded, orig_addr, fixups, labels, force_overflow))

    if textbuf_limit is not None:
        # Walk FFC0 chains and warn — caller may upgrade to error.
        def _chain_bytes(idx: int, visited: set[int]) -> int:
            if idx in visited or idx >= len(encoded_entries):
                return 0
            visited.add(idx)
            data, _, e_fixups, _, _ = encoded_entries[idx]
            total = len(data)
            for fixup in e_fixups:
                if fixup.entry_idx is not None:
                    total += _chain_bytes(fixup.entry_idx, visited)
            return total
        for entry_idx in range(len(encoded_entries)):
            if encoded_entries[entry_idx][0] == b'\x00':
                continue
            total = _chain_bytes(entry_idx, set())
            if total > textbuf_limit:
                # Surface via stdout to match LM3; build layer can promote later.
                print(
                    f'  WARNING: entry {entry_idx} chain {total}b > '
                    f'textbuf_limit {textbuf_limit}b'
                )

    return encoded_entries


# ---- Windowed script encoder ---------------------------------------------

_WINDOW_ENTRY_HEADER_RE = re.compile(r'^(<<\$[^>]+>>)', re.MULTILINE)
_WINDOW_BLOCK_RE = re.compile(
    r'<<<window\[(\d+)\]:\$([0-9A-Fa-f]+)-\$([0-9A-Fa-f]+)>>>\s*\n(.*?)'
    r'(?=<<<window|<<\$|$)',
    re.DOTALL,
)


def encode_windowed_script_file(
    script_file: Union[str, Path],
    table_filename: Union[str, Path],
    *,
    fallback_table: Optional[Union[str, Path]] = None,
) -> list[Optional[list[tuple[int, int, bytes]]]]:
    """Parse a windowed event-script file.

    Each entry may contain zero or more
    ``<<<window[N]:$START-$END>>>`` blocks. Returns a list indexed by header
    ``:N``; each slot is either ``None`` (no windows — pure bytecode) or a
    list of ``(start, end, encoded_bytes)`` triples. Encoded bytes exclude
    any trailing ``0x00`` terminator — the redirect back into original ROM
    already lands on the source [end] byte.
    """
    tbl = Table(str(table_filename))
    fb_tbl = Table(str(fallback_table)) if fallback_table else None
    text = _read_script_text(Path(script_file))

    parts = _WINDOW_ENTRY_HEADER_RE.split(text)
    parsed: dict[int, list[tuple[int, int, str]]] = {}
    current_header: Optional[str] = None
    for part in parts:
        m = _WINDOW_ENTRY_HEADER_RE.match(part)
        if m:
            current_header = part
            continue
        if current_header is None:
            continue
        rest = part
        header = current_header
        idx_match = re.search(r':(\d+)', header)
        if not idx_match:
            current_header = None
            continue
        header_idx = int(idx_match.group(1))
        windows: list[tuple[int, int, str]] = []
        for wm in _WINDOW_BLOCK_RE.finditer(rest):
            start = int(wm.group(2), 16)
            end = int(wm.group(3), 16)
            content = wm.group(4).rstrip('\n\r\t ')
            windows.append((start, end, content))
        if windows:
            parsed[header_idx] = windows
        current_header = None

    result: list[Optional[list[tuple[int, int, bytes]]]] = []
    if not parsed:
        return result
    max_idx = max(parsed)
    for entry_idx in range(max_idx + 1):
        if entry_idx not in parsed:
            result.append(None)
            continue
        encoded_windows: list[tuple[int, int, bytes]] = []
        for start, end, content in parsed[entry_idx]:
            if not content:
                encoded_windows.append((start, end, b''))
                continue
            encoded, _, _ = encode_text(content, tbl, fallback_table=fb_tbl)
            if encoded.endswith(b'\x00'):
                encoded = encoded[:-1]
            encoded_windows.append((start, end, encoded))
        result.append(encoded_windows)
    return result

"""Table file codec. .tbl format: `HH=char` lines, `**` variable substitution."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from math import log
from pathlib import Path
from typing import Optional, Union


_TABLE_CACHE: dict[tuple[str, int, int], "Table"] = {}
_TABLE_CACHE_LOCK = threading.Lock()

# Pre-computed two-digit uppercase hex strings 00..FF — used to expand
# `**`/`%%` wildcards in .tbl files without per-iteration f-string format.
_HEX2 = tuple(f'{i:02X}' for i in range(0x100))


@dataclass
class _CtrlEntry:
    """Per-prefix control-code descriptor.

    `default_length` is the total byte count consumed when the prefix byte
    is followed by a `cmd` byte not present in `cmds` (or when the prefix
    is standalone — set default_length=1 in that case). `cmds` maps cmd
    byte → total length, overriding the default for specific cmd bytes.

    Length INCLUDES the prefix byte itself. So a 1-byte standalone ctrl
    has length=1 (no cmd byte consumed). A 2-byte `prefix + cmd` has
    length=2 (prefix + 1 byte after). A 4-byte ctrl has length=4
    (prefix + 3 bytes after).
    """
    default_length: int = 3
    cmds: dict[int, int] = field(default_factory=dict)


def load_table(
    table_file: Union[str, Path],
    warn_duplicates: bool = False,
) -> "Table":
    """Process-wide cached Table loader keyed by (abspath, mtime_ns, size).

    Tables are immutable after construction, so a single instance can be
    shared across threads/sections. Stat is cheap; full parse is not (65k+
    entry wildcard expansion). Bypasses the cache when warn_duplicates is
    set so repeated calls still emit warnings.
    """
    if warn_duplicates:
        return Table(table_file, warn_duplicates=True)
    abs_path = os.path.abspath(os.fspath(table_file))
    try:
        st = os.stat(abs_path)
    except OSError:
        return Table(table_file)
    key = (abs_path, st.st_mtime_ns, st.st_size)
    cached = _TABLE_CACHE.get(key)
    if cached is not None:
        return cached
    with _TABLE_CACHE_LOCK:
        cached = _TABLE_CACHE.get(key)
        if cached is not None:
            return cached
        tbl = Table(table_file)
        _TABLE_CACHE[key] = tbl
        return tbl


class Table:
    """Ported from v0.1 retrotool/script.py. Loads .tbl, encodes/decodes bytes↔text."""

    def __init__(self, table_file: Union[str, Path], warn_duplicates: bool = False):
        (enc, val_map, char_map, char_bytes, ctrl_table, ctrl_prefixes,
         ctrl_types, err_count, cnt) = self._load_table(table_file)
        self.__val_map = val_map
        self.__chr_map = char_map
        self.__chr_bytes = char_bytes
        self.__ctrl_table: dict[int, _CtrlEntry] = ctrl_table
        self.__ctrl_prefixes: list[int] = ctrl_prefixes  # insertion order preserved
        self.__ctrl_types = ctrl_types
        self.__errors = err_count
        self.__parsed_lines = cnt
        self.__file_name = table_file
        self.__encoding = enc
        self.__max_key_len = max((len(k) for k in char_bytes), default=1)
        if warn_duplicates:
            self._check_duplicates()

    def _check_duplicates(self) -> None:
        """Warn about characters with multiple byte encodings (round-trip hazard)."""
        from collections import defaultdict
        char_to_vals: dict[str, list[int]] = defaultdict(list)
        for val, ch in self.__val_map.items():
            char_to_vals[ch].append(val)
        dupes = {ch: sorted(vals) for ch, vals in char_to_vals.items() if len(vals) > 1}
        if dupes:
            print(f'WARNING: {self.__file_name} has {len(dupes)} characters '
                  f'with duplicate encodings (round-trip mismatch risk):')
            for ch, vals in sorted(dupes.items(), key=lambda x: x[1][0]):
                hex_vals = ', '.join(f'${v:04X}' for v in vals)
                used = self.__chr_map.get(ch)
                print(f'  {ch!r:8s}: {hex_vals}  (encoder uses ${used:04X})')

    def _load_table(self, table_file, enc=None):
        enc = enc if enc is not None else self.detect_encoding(table_file)
        val_map: dict[int, str] = {}
        char_map: dict[str, int] = {}
        # Sibling map preserving each hex-code's declared byte width. The
        # `char_map: int` path silently drops leading-zero bytes on
        # serialization (e.g. `000A=X` → `0x0A` → b'\x0A', not b'\x00\x0A').
        # Encoders should prefer `char_bytes` for byte-faithful output.
        char_bytes: dict[str, bytes] = {}
        ctrl_table: dict[int, _CtrlEntry] = {}
        ctrl_prefixes: list[int] = []          # insertion-ordered for stability
        ctrl_types: dict[int, str] = {}        # flat cmd→type for backcompat
        ctrl_prefix_declared = False           # first @ctrl_prefix replaces default
        err_count = 0
        cnt = 1
        with open(table_file, encoding=enc) as to:
            first = True
            for line in to:
                if first:
                    if line.startswith('\ufeff'):
                        line = line[1:]
                    first = False
                try:
                    stripped = line.strip()
                    if not stripped or stripped.startswith(';'):
                        cnt += 1
                        continue
                    # @ctrl_prefix XX [YY ZZ ...]: declares control-code prefix
                    # bytes. Default $FF if no @ctrl_prefix line is present. The
                    # FIRST @ctrl_prefix line resets the prefix list (so a
                    # single-prefix table behaves as before); subsequent lines
                    # are additive. Multi-prefix lets games where F7-FE each
                    # act as an independent opcode declare them all.
                    if stripped.startswith('@ctrl_prefix '):
                        for tok in stripped[len('@ctrl_prefix '):].split():
                            pb = int(tok, 16)
                            if not ctrl_prefix_declared:
                                ctrl_prefixes = []
                                ctrl_prefix_declared = True
                            if pb not in ctrl_prefixes:
                                ctrl_prefixes.append(pb)
                            ctrl_table.setdefault(pb, _CtrlEntry())
                        cnt += 1
                        continue
                    # @ctrl directive — declares a per-prefix length entry.
                    # Forms:
                    #   @ctrl XX=N            — XX is a cmd byte under the
                    #                            (first/default) prefix; length N
                    #                            includes the prefix byte.
                    #   @ctrl XX**=N          — same; trailing `**` is legacy
                    #                            wildcard syntax preserved for
                    #                            backcompat (the cmd-byte lookup
                    #                            ignores the wildcard position).
                    #   @ctrl PP.XX=N         — explicit per-prefix cmd entry;
                    #                            applies when prefix byte is PP
                    #                            and cmd byte is XX.
                    #   @ctrl PP=N            — when PP is a declared
                    #                            @ctrl_prefix, sets that prefix's
                    #                            default length (used when the
                    #                            cmd-byte lookup misses).
                    if stripped.startswith('@ctrl '):
                        rest = stripped[6:].strip()
                        tokens = rest.split()
                        head = tokens[0] if tokens else ''
                        if '=' in head:
                            pattern, length_s = head.split('=', 1)
                            pattern = pattern.strip().rstrip('*')  # drop trailing **
                            length = int(length_s.strip())
                            ctype = None
                            for extra in tokens[1:]:
                                if extra.startswith('type='):
                                    ctype = extra[5:].strip()
                            default_prefix = ctrl_prefixes[0] if ctrl_prefixes else 0xFF
                            if '.' in pattern:
                                # PP.XX form — explicit per-prefix cmd entry.
                                ps, cs = pattern.split('.', 1)
                                pb = int(ps, 16)
                                cmd_byte = int(cs, 16)
                                if pb not in ctrl_prefixes:
                                    ctrl_prefixes.append(pb)
                                entry = ctrl_table.setdefault(pb, _CtrlEntry())
                                entry.cmds[cmd_byte] = length
                                if ctype is not None:
                                    ctrl_types[cmd_byte] = ctype
                            else:
                                byte_val = int(pattern, 16)
                                if byte_val in ctrl_prefixes:
                                    # PP=N — set this prefix's default length.
                                    entry = ctrl_table.setdefault(byte_val, _CtrlEntry())
                                    entry.default_length = length
                                else:
                                    # XX=N — cmd byte under default prefix.
                                    entry = ctrl_table.setdefault(default_prefix, _CtrlEntry())
                                    entry.cmds[byte_val] = length
                                    if ctype is not None:
                                        ctrl_types[byte_val] = ctype
                        cnt += 1
                        continue
                    parts = line.split('=')
                    if len(parts) == 2:
                        val, ch = parts
                        ch = ch.replace('\n', '').replace('\r', '').replace('\\n', '\n')
                        # PuttScript-style `.tbl` files use `%%` as the primary
                        # wildcard (retrotool's native syntax uses `**` primary
                        # + `%%` secondary). Normalize to retrotool's form so
                        # the rest of the parser handles both styles. Only
                        # touch lines where `**` is absent; if both `**` and
                        # `%%` are already present, the user is on retrotool's
                        # two-wildcard syntax and we leave it alone.
                        if '%%' in val and '**' not in val:
                            # `%%%%` (4 percents) is PuttScript's "two
                            # consecutive wildcards = a 2-byte position".
                            # Map to retrotool's `**%%` (primary + secondary).
                            # `%%` (2 percents) is a single 1-byte wildcard.
                            val = val.replace('%%%%', '**\x00').replace('%%', '**').replace('\x00', '%%')
                            ch = ch.replace('%%%%', '**\x00').replace('%%', '**').replace('\x00', '%%')
                        if '**' in val:
                            self._expand_wildcards(
                                val, ch, val_map, char_map, char_bytes,
                            )
                        else:
                            self._set_maps(val, ch, val_map, char_map, char_bytes)
                except Exception as ex:
                    print(f"ERROR: {ex!r}")
                    err_count += 1
                cnt += 1
        # If no @ctrl_prefix line appeared but @ctrl cmd entries did, fall
        # back to the historical default prefix $FF so single-prefix tables
        # written before this multi-prefix shape still parse the same way.
        if not ctrl_prefix_declared and ctrl_table:
            ctrl_prefixes = [0xFF]
            ctrl_table.setdefault(0xFF, _CtrlEntry())
        return enc, val_map, char_map, char_bytes, ctrl_table, ctrl_prefixes, ctrl_types, err_count, cnt

    @staticmethod
    def _set_maps(in_val, in_ch, val_map, char_map, char_bytes=None):
        dec_val = int(in_val, 16)
        if dec_val not in val_map:
            val_map[dec_val] = in_ch
        if in_ch not in char_map:
            char_map[in_ch] = dec_val
        if char_bytes is not None and in_ch not in char_bytes:
            hex_str = in_val if len(in_val) % 2 == 0 else '0' + in_val
            char_bytes[in_ch] = bytes.fromhex(hex_str)

    @staticmethod
    def _expand_wildcards(val, ch, val_map, char_map, char_bytes):
        """Expand `**`/`%%` wildcard lines into 256/65536 entries.

        Hot path for tables like LM3's eng.tbl which use `XX**=Y**` to define
        a full 256-entry block. Uses pre-computed hex LUT and bypasses
        _set_maps's per-call int parsing — for 65k+ entries the function-call
        and string-parse overhead dominated table load time before this.
        """
        hex2 = _HEX2
        has_pct = '%%' in val
        # Resolve `**` once outside the loop. `str.replace` with a small
        # pattern is faster than rebuilding the string per iteration with
        # slicing because we benefit from the C-level replace impl.
        # `int(s, 16)` and `bytes.fromhex(s)` are still called per entry —
        # they are C builtins and dominate vs. the surrounding Python.
        for d in range(0x100):
            hd = hex2[d]
            v1 = val.replace('**', hd)
            c1 = ch.replace('**', hd)
            if has_pct:
                for e in range(0x100):
                    he = hex2[e]
                    v2 = v1.replace('%%', he)
                    c2 = c1.replace('%%', he)
                    dec = int(v2, 16)
                    if dec not in val_map:
                        val_map[dec] = c2
                    if c2 not in char_map:
                        char_map[c2] = dec
                    if c2 not in char_bytes:
                        hs = v2 if len(v2) % 2 == 0 else '0' + v2
                        char_bytes[c2] = bytes.fromhex(hs)
            else:
                dec = int(v1, 16)
                if dec not in val_map:
                    val_map[dec] = c1
                if c1 not in char_map:
                    char_map[c1] = dec
                if c1 not in char_bytes:
                    hs = v1 if len(v1) % 2 == 0 else '0' + v1
                    char_bytes[c1] = bytes.fromhex(hs)

    @property
    def encoding(self) -> Optional[str]:
        return self.__encoding

    @property
    def errors(self) -> int:
        return self.__errors

    @property
    def val_map(self) -> dict[int, str]:
        return self.__val_map

    @property
    def char_map(self) -> dict[str, int]:
        return self.__chr_map

    @property
    def max_key_len(self) -> int:
        """Longest key (in characters) across char_bytes. Cached at construction.

        Used by encode_text() to bound the longest-match search; computing this
        per-call was the dominant cost on tables with `**` wildcard expansion
        (65k+ entries). See Plans/native-encoder-profile-results.md.
        """
        return self.__max_key_len

    @property
    def char_bytes(self) -> dict[str, bytes]:
        """char → raw bytes preserving the declared hex-code byte width.

        Prefer this over `char_map` for encoders: `char_map` stores the
        value as `int`, which silently drops leading-zero bytes when
        serialized (e.g. `000A=X` → `0x0A` → one byte out, not two).
        """
        return self.__chr_bytes

    @property
    def ctrl_prefix(self) -> int:
        """Primary control-code prefix byte (default $FF).

        For single-prefix tables this is the byte declared by
        `@ctrl_prefix XX`. For multi-prefix tables this is the FIRST
        prefix declared — kept as a property for backward compatibility
        with code that assumed exactly one prefix. New multi-prefix-aware
        code should consult `ctrl_prefixes` instead.
        """
        return self.__ctrl_prefixes[0] if self.__ctrl_prefixes else 0xFF

    @property
    def ctrl_prefixes(self) -> list[int]:
        """All declared control-code prefix bytes, in declaration order.

        Empty list means no `@ctrl_prefix` directive was found AND no
        `@ctrl` cmd entries were declared. Tables declaring `@ctrl` cmds
        without an explicit `@ctrl_prefix` fall back to `[0xFF]` for
        backward compatibility with the single-prefix-only era.
        """
        return list(self.__ctrl_prefixes)

    @property
    def ctrl_types(self) -> dict[int, str]:
        """Control-code semantic tags parsed from `@ctrl XX=N type=NAME` lines.

        Single-prefix shape: cmd byte → tag string. Multi-prefix tables
        still surface a flat cmd→type view here (tags collide silently if
        the same cmd byte recurs under different prefixes); use the
        per-prefix `ctrl_table()` to disambiguate.
        """
        return self.__ctrl_types

    @property
    def ctrl_lengths(self) -> dict[int, int]:
        """Flat cmd-byte → total-length view for the primary prefix.

        Backward-compatible single-prefix shape. For multi-prefix tables,
        this returns only the cmd entries declared under the FIRST prefix;
        use `ctrl_lookup(prefix, cmd)` or `ctrl_table()` for full coverage.

        Length includes the prefix byte itself.
        """
        if not self.__ctrl_prefixes:
            return {}
        entry = self.__ctrl_table.get(self.__ctrl_prefixes[0])
        return dict(entry.cmds) if entry else {}

    def ctrl_lookup(self, prefix: int, cmd: Optional[int] = None) -> Optional[int]:
        """Return total ctrl length for `prefix` (+ optional `cmd`).

        Returns `None` if `prefix` is not a declared @ctrl_prefix byte.
        Otherwise: if `cmd` is None or has no per-cmd override, returns
        the prefix's `default_length`; if `cmd` matches an entry, returns
        the override length.
        """
        entry = self.__ctrl_table.get(prefix)
        if entry is None:
            return None
        if cmd is None:
            return entry.default_length
        return entry.cmds.get(cmd, entry.default_length)

    def ctrl_table(self) -> dict[int, tuple[int, dict[int, int]]]:
        """Snapshot of the per-prefix control-code table.

        Maps prefix byte → (default_length, {cmd_byte: length, ...}).
        Returned tuples are copies; modifying them does not affect the
        Table's internal state.
        """
        return {
            p: (e.default_length, dict(e.cmds))
            for p, e in self.__ctrl_table.items()
        }

    def get_value(self, word: str, infer_value: bool = True) -> Optional[int]:
        if not isinstance(word, str):
            raise ValueError("Value must be a string!")
        if word in self.__chr_map:
            return self.__chr_map[word]
        if infer_value and '[' in word and ']' in word:
            try:
                return int(word.replace('[', '').replace(']', ''), 16)
            except ValueError:
                pass
        return None

    def get_chars(self, value: int, return_hex_repr: bool = True) -> Optional[str]:
        if value in self.__val_map:
            return self.__val_map[value]
        return f'[{self.hex(value)}]' if return_hex_repr else None

    @staticmethod
    def hex(value: int) -> str:
        if value < 0x100:
            pad = 2
        elif value < 0x10000:
            pad = 4
        elif value < 0x1000000:
            pad = 6
        elif value < 0x100000000:
            pad = 8
        else:
            raise ValueError("Error: Table Value is not supported!")
        return f'{value:0{pad}X}'

    @staticmethod
    def byte_size(value: int) -> int:
        if value == 0:
            return 1
        return int(log(value, 256)) + 1

    @staticmethod
    def bytes_to_val(byte_list: list[int], reverse: bool = False) -> int:
        if reverse:
            byte_list = list(reversed(byte_list))
        out = 0
        for i, b in enumerate(byte_list):
            out |= b << (i * 8)
        return out

    def interpret_binary(self, input_filename, max_bytes: int = 3) -> str:
        with open(input_filename, "rb") as f:
            bin_data = list(f.read())
        return self.interpret_binary_data(bin_data, max_bytes)

    def interpret_binary_data(self, bin_data: list[int], max_bytes: int = 3,
                              trim_bytes: Optional[Union[int, list[int]]] = None) -> str:
        if trim_bytes is not None:
            if isinstance(trim_bytes, int):
                trim_bytes = [trim_bytes]
            exclude = 0
            for b in reversed(bin_data):
                if b in trim_bytes:
                    exclude += 1
                else:
                    break
            if exclude:
                bin_data = bin_data[:-exclude]

        final = ''
        i = 0
        n = len(bin_data)
        ctrl_table = self.__ctrl_table
        ctrl_prefixes = self.__ctrl_prefixes
        while i <= n + 1:
            if i >= n:
                break
            # Control sequence: emit the full declared span as a single
            # `[HH..]` hex escape so round-trip preserves the ctrl
            # payload. `find_entry_end` already walks ctrls this way;
            # the plain decode path used to ignore them, splitting
            # payload bytes across hex escape + literal char decodes.
            #
            # Multi-prefix lookup: when bin_data[i] matches any declared
            # ctrl-prefix byte, consume its per-prefix length. For PP+cmd
            # ctrls the cmd byte is bin_data[i+1]; for standalone PP ctrls
            # (length=1) no cmd is consumed.
            cur_byte = bin_data[i]
            if cur_byte in ctrl_prefixes:
                entry = ctrl_table[cur_byte]
                if entry.default_length <= 1:
                    span = entry.default_length
                elif i + 1 < n:
                    span = entry.cmds.get(bin_data[i + 1], entry.default_length)
                else:
                    span = 1
                end = min(i + span, n)
                final += self.hex_dump(bin_data[i:end])
                i = end
                continue
            length = max_bytes
            char = None
            found = False
            while length > 0:
                val = self.bytes_to_val(bin_data[i:i + length], True)
                char = self.get_chars(val, False)
                if char:
                    # Guard against length-mismatch matches: when the
                    # current window starts with one or more 0x00 bytes,
                    # bytes_to_val collapses them into a smaller integer
                    # that can collide with a shorter real entry (e.g.
                    # `[0x00, 0x39]` → val 0x39, which would falsely
                    # match the 1-byte `0x39='r'` entry and silently
                    # drop the leading 0x00). Reject the match when the
                    # actual byte width of the matched character (from
                    # char_bytes) differs from the window length.
                    cb = self.__chr_bytes.get(char)
                    if cb is not None and len(cb) != length:
                        char = None
                    else:
                        found = True
                        i += (length - 1)
                        break
                length -= 1
            if not found:
                char = self.get_chars(bin_data[i], True)
            if char is None:
                print(f"ERROR - Unable to resolve byte ({hex(bin_data[i])})???")
            else:
                final += char
            i += 1
        return final

    def has_char(self, bin_data: list[int]) -> Optional[str]:
        val = self.bytes_to_val(bin_data)
        if len(bin_data) > 1 and val in bin_data:
            return None
        return self.get_chars(val, False)

    @staticmethod
    def detect_encoding(file_path, lines: int = 80) -> Optional[str]:
        import chardet
        with open(file_path, 'rb') as f:
            raw = b''.join(f.readline() for _ in range(lines))
        return chardet.detect(raw)['encoding']

    @staticmethod
    def export_csv(filename: Union[str, Path], dict_data: list[dict]) -> None:
        """Write list-of-dicts to `{filename}.csv` using the first row's keys as header."""
        import csv
        if not dict_data:
            return
        csv_columns = list(dict_data[0].keys())
        csv_file = f"./{filename}.csv"
        try:
            with open(csv_file, 'w', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=csv_columns)
                writer.writeheader()
                for row in dict_data:
                    writer.writerow(row)
        except IOError:
            print("I/O error")

    def check_for_lone_byte(self, bin_data, index, value: int = 0x0):
        """Check for lone terminator byte; confirms it's not part of multi-byte value."""
        start1 = index - 3
        start2 = index - 2
        end0 = index + 1
        if bin_data[index] == value:
            char1 = self.has_char(bin_data[start1:end0])
            char2 = self.has_char(bin_data[start2:end0])
            if char1 is not None or char2 is not None:
                return 0, char1 or char2
            return -1, None
        return 0, None

    def find_entry_end(self, bin_data, start: int, max_bytes: int = 3,
                       max_addr: Optional[int] = None,
                       terminator: int = 0x00) -> int:
        """Left-to-right decode to find the entry terminator. Uses @ctrl
        lengths so parameter bytes inside control-code sequences aren't
        mistaken for terminators. `terminator` defaults to $00; set to any
        byte value for games that terminate with a different sentinel.

        Multi-prefix tables: every byte declared via @ctrl_prefix triggers
        a per-prefix length lookup. For prefix entries with default_length=1
        (standalone control codes), only the prefix byte is consumed.
        """
        ctrl_table = self.__ctrl_table
        ctrl_prefixes = self.__ctrl_prefixes
        i = start
        n = len(bin_data)
        while i < n:
            if max_addr is not None and i >= max_addr:
                return i
            cur_byte = bin_data[i]
            if cur_byte in ctrl_prefixes:
                entry = ctrl_table[cur_byte]
                if entry.default_length <= 1:
                    ctrl_len = entry.default_length
                elif i + 1 < n:
                    ctrl_len = entry.cmds.get(bin_data[i + 1], entry.default_length)
                else:
                    ctrl_len = 1
                # Standalone single-byte ctrl that also equals the terminator
                # marks end-of-entry (rbshura's $FF STOP works this way).
                if ctrl_len == 1 and cur_byte == terminator:
                    return i + 1
                i += ctrl_len
                continue
            matched = False
            for size in range(max_bytes, 1, -1):
                if i + size > n:
                    continue
                window = list(bin_data[i:i + size])
                # Don't fold a ctrl-prefix byte into a multi-byte char — its
                # role belongs to the next ctrl sequence, not this entry.
                if size > 1 and window[-1] in ctrl_prefixes:
                    continue
                val = self.bytes_to_val(window, True)
                if self.byte_size(val) != size:
                    continue
                if self.get_chars(val, False) is not None:
                    i += size
                    matched = True
                    break
            if not matched:
                if cur_byte == terminator:
                    return i + 1
                i += 1
        return i

    @staticmethod
    def _is_binary_block(decoded_str: str, raw_data=None) -> bool:
        """Heuristic: decoded output mostly bracketed control codes, or raw has interior $00s."""
        in_bracket = 0
        out_bracket = 0
        inside = False
        for ch in decoded_str:
            if ch == '[':
                inside = True
            elif ch == ']':
                inside = False
                in_bracket += 1
            elif inside:
                pass
            else:
                out_bracket += 1
        total = in_bracket + out_bracket
        if total > 0 and in_bracket > out_bracket:
            return True
        if raw_data and len(raw_data) > 1:
            interior = raw_data[:-1] if raw_data[-1] == 0 else raw_data
            zero_count = sum(1 for b in interior if b == 0)
            if zero_count > 0 and zero_count >= len(interior) * 0.15:
                return True
        return False

    @staticmethod
    def hex_dump(bin_data) -> str:
        """Render bytes as bracketed hex escapes: [04][00][B3]..."""
        return ''.join(f'[{b:02X}]' for b in bin_data)

    def dump_script(self, filename: Union[str, Path], dict_data: list,
                    deduplicate: bool = True) -> None:
        """Dump decoded script entries to UTF-16 file; falls back to hex_dump for binary blocks."""
        line1 = True
        nl = "\n"
        with open(filename, 'w', encoding='utf-16') as of:
            dumped_addrs = []
            for data in dict_data:
                of.write(f"{'' if line1 else nl}<<{data.get('id')}>>{nl}")
                addr = data.get('addr', None)
                should_write = True
                if deduplicate and addr is not None:
                    if addr in dumped_addrs:
                        should_write = False
                    else:
                        dumped_addrs.append(addr)
                if should_write:
                    raw = data['data']
                    decoded = self.interpret_binary_data(raw)
                    if self._is_binary_block(decoded, raw):
                        of.write(self.hex_dump(raw))
                    else:
                        of.write(decoded)
                line1 = False

    # ------------------------------------------------------------------
    # v2 additions

    def encode_text(self, text: str, max_token_len: Optional[int] = None) -> bytes:
        """Longest-match encode text → bytes. Honors `[HH]` hex literals.

        `max_token_len` defaults to the longest text key declared in the
        table (`self.max_key_len`). Capped at 4 unless the table actually
        declares longer multi-character tokens, so simple tables don't pay
        for an O(n*max_key_len) scan when nothing in the table needs it.

        Multi-byte values (>3 bytes — e.g. a `FD1F...FD42=...` pluck
        sequence) are emitted byte-faithfully via `char_bytes`, which
        preserves the declared hex-code byte width even where the integer
        representation would need more than 3 bytes.
        """
        if max_token_len is None:
            max_token_len = max(self.max_key_len, 4)
        out = bytearray()
        i = 0
        n = len(text)
        char_bytes = self.char_bytes
        char_map = self.char_map
        while i < n:
            if text[i] == '[':
                end = text.find(']', i)
                if end == -1:
                    raise ValueError(f"Unterminated [ at pos {i}")
                token = text[i:end + 1]
                # Prefer char_bytes for byte-faithful emission of declared
                # tokens (handles >3-byte sequences). Fall back to int via
                # get_value+_emit_value for inferred [HH] hex literals.
                cb = char_bytes.get(token)
                if cb is not None:
                    out.extend(cb)
                else:
                    val = self.get_value(token, infer_value=True)
                    if val is None:
                        raise ValueError(f"Unknown token {token!r} at pos {i}")
                    _emit_value(out, val)
                i = end + 1
                continue
            matched = False
            for plen in range(min(max_token_len, n - i), 0, -1):
                candidate = text[i:i + plen]
                cb = char_bytes.get(candidate)
                if cb is not None:
                    out.extend(cb)
                    i += plen
                    matched = True
                    break
                val = char_map.get(candidate)
                if val is not None:
                    _emit_value(out, val)
                    i += plen
                    matched = True
                    break
            if not matched:
                raise ValueError(f"No encoding for {text[i]!r} at pos {i}")
        return bytes(out)


def _emit_value(out: bytearray, val: int) -> None:
    """Emit big-endian representation of a multi-byte integer value.

    Capped at 3 bytes by historical table convention. For values larger
    than 3 bytes (declared via long hex codes like `FD1FFD43...`), the
    encoder reaches `char_bytes` directly instead of routing through int.
    """
    if val <= 0xFF:
        out.append(val)
    elif val <= 0xFFFF:
        out.append((val >> 8) & 0xFF)
        out.append(val & 0xFF)
    elif val <= 0xFFFFFF:
        out.append((val >> 16) & 0xFF)
        out.append((val >> 8) & 0xFF)
        out.append(val & 0xFF)
    else:
        raise ValueError(f"Value {val:#X} exceeds 3 bytes")

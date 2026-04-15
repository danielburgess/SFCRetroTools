"""Table file codec. .tbl format: `HH=char` lines, `**` variable substitution."""
from __future__ import annotations

from math import log
from pathlib import Path
from typing import Optional, Union


class Table:
    """Ported from v0.1 retrotool/script.py. Loads .tbl, encodes/decodes bytes↔text."""

    def __init__(self, table_file: Union[str, Path], warn_duplicates: bool = False):
        enc, val_map, char_map, ctrl_lengths, ctrl_types, ctrl_prefix, err_count, cnt = self._load_table(table_file)
        self.__val_map = val_map
        self.__chr_map = char_map
        self.__ctrl_lengths = ctrl_lengths
        self.__ctrl_types = ctrl_types
        self.__ctrl_prefix = ctrl_prefix
        self.__errors = err_count
        self.__parsed_lines = cnt
        self.__file_name = table_file
        self.__encoding = enc
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
        ctrl_lengths: dict[int, int] = {}
        ctrl_types: dict[int, str] = {}
        ctrl_prefix: int = 0xFF
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
                    # @ctrl_prefix XX: sets the control-code prefix byte
                    # (default $FF). Must appear before any @ctrl entries to
                    # take effect; otherwise the default is used.
                    if stripped.startswith('@ctrl_prefix '):
                        ctrl_prefix = int(stripped[len('@ctrl_prefix '):].strip(), 16)
                        cnt += 1
                        continue
                    # @ctrl directive: @ctrl XX=N defines control code <prefix> XX
                    # with total byte length N (including the prefix).
                    # Wildcards: @ctrl XX**=N applies to all <prefix> XX yy.
                    if stripped.startswith('@ctrl '):
                        # Syntax:  @ctrl XX=N [type=NAME]
                        # XX may use '**' as a wildcard second nibble.
                        rest = stripped[6:].strip()
                        tokens = rest.split()
                        head = tokens[0] if tokens else ''
                        if '=' in head:
                            pattern, length_s = head.split('=', 1)
                            pattern = pattern.strip()
                            length = int(length_s.strip())
                            ctype = None
                            for extra in tokens[1:]:
                                if extra.startswith('type='):
                                    ctype = extra[5:].strip()
                            if '**' in pattern:
                                prefix = int(pattern.replace('**', ''), 16)
                                for d in range(0x100):
                                    key = prefix * 0x100 + d
                                    ctrl_lengths[key] = length
                                    if ctype is not None:
                                        ctrl_types[key] = ctype
                            else:
                                key = int(pattern, 16)
                                ctrl_lengths[key] = length
                                if ctype is not None:
                                    ctrl_types[key] = ctype
                        cnt += 1
                        continue
                    parts = line.split('=')
                    if len(parts) == 2:
                        val, ch = parts
                        ch = ch.replace('\n', '').replace('\r', '').replace('\\n', '\n')
                        if '**' in val:
                            for d in range(0x100):
                                prep_ch = ch.replace('**', self.hex(d))
                                prep_val = val.replace('**', self.hex(d))
                                if '%%' in val:
                                    for e in range(0x100):
                                        self._set_maps(
                                            prep_val.replace('%%', self.hex(e)),
                                            prep_ch.replace('%%', self.hex(e)),
                                            val_map, char_map,
                                        )
                                else:
                                    self._set_maps(prep_val, prep_ch, val_map, char_map)
                        else:
                            self._set_maps(val, ch, val_map, char_map)
                except Exception as ex:
                    print(f"ERROR: {ex!r}")
                    err_count += 1
                cnt += 1
        return enc, val_map, char_map, ctrl_lengths, ctrl_types, ctrl_prefix, err_count, cnt

    @staticmethod
    def _set_maps(in_val, in_ch, val_map, char_map):
        dec_val = int(in_val, 16)
        if dec_val not in val_map:
            val_map[dec_val] = in_ch
        if in_ch not in char_map:
            char_map[in_ch] = dec_val

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
    def ctrl_prefix(self) -> int:
        """Control-code prefix byte (default $FF). Set via `@ctrl_prefix XX` directive."""
        return self.__ctrl_prefix

    @property
    def ctrl_types(self) -> dict[int, str]:
        """Control-code semantic tags parsed from `@ctrl XX=N type=NAME` lines.

        Keys match `ctrl_lengths` keys. Values are free-form names understood
        by downstream consumers (e.g. `"redirect"` for FF-redirect opcodes
        like FFC0/FFF7). Missing entries have no declared type."""
        return self.__ctrl_types

    @property
    def ctrl_lengths(self) -> dict[int, int]:
        """FF-control-code byte lengths parsed from @ctrl directives.

        Keys: command byte(s) following the FF prefix. Values: total byte
        length including the FF prefix itself. Prevents decoders from
        splitting on 0x00 bytes that appear inside FF-command parameters."""
        return self.__ctrl_lengths

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
        while i <= n + 1:
            if i >= n:
                break
            length = max_bytes
            char = None
            found = False
            while length > 0:
                val = self.bytes_to_val(bin_data[i:i + length], True)
                char = self.get_chars(val, False)
                if char:
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
                       max_addr: Optional[int] = None) -> int:
        """Left-to-right decode to find terminating $00. Uses @ctrl lengths so
        parameter bytes inside FF sequences aren't mistaken for terminators."""
        ctrl = self.__ctrl_lengths
        prefix = self.__ctrl_prefix
        i = start
        while i < len(bin_data):
            if max_addr is not None and i >= max_addr:
                return i
            if bin_data[i] == prefix and i + 1 < len(bin_data):
                cmd = bin_data[i + 1]
                ctrl_len = ctrl.get(cmd, 3)
                i += ctrl_len
                continue
            matched = False
            for size in range(max_bytes, 1, -1):
                if i + size > len(bin_data):
                    continue
                window = list(bin_data[i:i + size])
                if size > 1 and window[-1] == 0xFF:
                    continue
                val = self.bytes_to_val(window, True)
                if self.byte_size(val) != size:
                    continue
                if self.get_chars(val, False) is not None:
                    i += size
                    matched = True
                    break
            if not matched:
                if bin_data[i] == 0x00:
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

    def encode_text(self, text: str, max_token_len: int = 4) -> bytes:
        """Longest-match encode text → bytes. Honors [HH] hex literals."""
        out = bytearray()
        i = 0
        n = len(text)
        while i < n:
            if text[i] == '[':
                end = text.find(']', i)
                if end == -1:
                    raise ValueError(f"Unterminated [ at pos {i}")
                token = text[i:end + 1]
                val = self.get_value(token, infer_value=True)
                if val is None:
                    raise ValueError(f"Unknown token {token!r} at pos {i}")
                _emit_value(out, val)
                i = end + 1
                continue
            matched = False
            for plen in range(min(max_token_len, n - i), 0, -1):
                candidate = text[i:i + plen]
                val = self.char_map.get(candidate)
                if val is not None:
                    _emit_value(out, val)
                    i += plen
                    matched = True
                    break
            if not matched:
                raise ValueError(f"No encoding for {text[i]!r} at pos {i}")
        return bytes(out)


def _emit_value(out: bytearray, val: int) -> None:
    """Emit big-endian representation of a multi-byte table value."""
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

"""Table file codec. .tbl format: `HH=char` lines, `**` variable substitution."""
from __future__ import annotations

from math import log
from pathlib import Path
from typing import Optional, Union


class Table:
    """Ported from v0.1 retrotool/script.py. Loads .tbl, encodes/decodes bytes↔text."""

    def __init__(self, table_file: Union[str, Path]):
        enc, val_map, char_map, err_count, cnt = self._load_table(table_file)
        self.__val_map = val_map
        self.__chr_map = char_map
        self.__errors = err_count
        self.__parsed_lines = cnt
        self.__file_name = table_file
        self.__encoding = enc

    def _load_table(self, table_file, enc=None):
        enc = enc if enc is not None else self.detect_encoding(table_file)
        val_map: dict[int, str] = {}
        char_map: dict[str, int] = {}
        err_count = 0
        cnt = 1
        with open(table_file, encoding=enc) as to:
            for line in to:
                try:
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
        return enc, val_map, char_map, err_count, cnt

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

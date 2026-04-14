"""SFCPointer — 24-bit SNES pointer (low/high/bank) with flexible setters."""
from __future__ import annotations

from typing import Union

from retrotool.core.binary import (
    bank_byte,
    hex_fmt,
    high_byte,
    integer_or_hex,
    low_byte,
)


class SFCPointer:
    def __init__(self, low=None, high=None, bank=None):
        """
        Low/high/bank accept overflows that cascade: low > 0xFFFF splits across all 3;
        high > 0xFF splits into high+bank.
        """
        self.__full_pointer = [0x0, 0x0, 0x0]
        valid = self.validate_bytes(low, high, bank)
        self.__set_ptr_pos(0, valid)
        self.__set_ptr_pos(1, valid)
        self.__set_ptr_pos(2, valid)

    def __str__(self):
        return hex_fmt(self.full_address, 6) if self.full_address > 0xFFFF else hex_fmt(self.short_address, 4)

    def __repr__(self):
        return str(self)

    @classmethod
    def validate_bytes(cls, *args):
        low = args[0] if len(args) > 0 and args[0] is not None else 0x0
        high = args[1] if len(args) > 1 and args[1] is not None else 0x0
        bank = args[2] if len(args) > 2 and args[2] is not None else 0x0
        if low:
            low = integer_or_hex(low, 0xFFFFFF)
            if low > 0xFFFF and not (high and bank):
                bank = bank_byte(low)
                high = high_byte(low)
                low = low_byte(low)
            elif low > 0xFF and not high:
                high = high_byte(low)
                low = low_byte(low)
        if high:
            high = integer_or_hex(high, 0xFFFF)
            if high > 0xFF and not bank:
                bank = high_byte(high)
                high = low_byte(high)
        if bank:
            bank = integer_or_hex(bank)
        return low, high, bank

    # Kept as staticmethods on class for backward-compat with callers
    integer_or_hex = staticmethod(integer_or_hex)
    hex_fmt = staticmethod(hex_fmt)

    @property
    def full_address(self):
        return self.__full_pointer[0] + (self.__full_pointer[1] << 8) + (self.__full_pointer[2] << 16)

    @property
    def full_hex(self):
        return hex_fmt(self.full_address, 6)

    @property
    def short_address(self):
        return self.__full_pointer[0] + (self.__full_pointer[1] << 8)

    @property
    def short_hex(self):
        return hex_fmt(self.short_address)

    @property
    def short(self):
        return self.__full_pointer[:2]

    @short.setter
    def short(self, value):
        self.__full_pointer = [0x0, 0x0, 0x0]
        self.__check_list_tuple(value)
        self.__set_ptr_pos(0, value)
        self.__set_ptr_pos(1, value)

    @property
    def full(self):
        return self.__full_pointer

    @full.setter
    def full(self, value):
        self.__full_pointer = [0x0, 0x0, 0x0]
        self.__check_list_tuple(value)
        self.__set_ptr_pos(0, value)
        self.__set_ptr_pos(1, value)
        self.__set_ptr_pos(2, value)

    @property
    def low(self):
        return self.__full_pointer[0]

    @low.setter
    def low(self, value):
        self.__full_pointer[0] = integer_or_hex(value)

    @property
    def high(self):
        return self.__full_pointer[1]

    @high.setter
    def high(self, value):
        self.__full_pointer[1] = integer_or_hex(value)

    @property
    def bank(self):
        return self.__full_pointer[2]

    @bank.setter
    def bank(self, value):
        self.__full_pointer[2] = integer_or_hex(value)

    def to_addr(self, addr_type):
        from retrotool.core.address import SFCAddress
        return SFCAddress(self.full_address, addr_type)

    @staticmethod
    def __check_list_tuple(value):
        if not isinstance(value, (list, tuple)):
            raise ValueError("Cannot assign any value but type of list or tuple.")
        if len(value) < 1:
            raise ValueError("List/Tuple length must be at least 1.")

    def __set_ptr_pos(self, index, input_val):
        if len(input_val) > index:
            self.__full_pointer[index] = integer_or_hex(input_val[index])

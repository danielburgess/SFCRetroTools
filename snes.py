from typing import Optional, Union
from functools import lru_cache
"""
Version 2021.3
by DackR
"""


class SFCPointer:
    def __init__(self, low=None, high=None, bank=None):
        """
        Pointers can be defined, modified, and read in many ways.
        low and high values can be used to fill part of, or the entire address, depending on what is desired
        :param low: can be as small as 8 bit, as big as 24 bit (over 24 bit is ignored)
        :param high: can be 8 to 16 bit (over 16 bit is ignored)
        :param bank: only be 8 bit (extra data is lost)
        """
        self.__full_pointer = [0x0, 0x0, 0x0]
        valid = self.validate_bytes(low, high, bank)
        self.__set_ptr_pos(0, valid)
        self.__set_ptr_pos(1, valid)
        self.__set_ptr_pos(2, valid)

    def __str__(self):
        """
        return the formatted address
        """
        return self.hex_fmt(self.full_address, 6) if self.full_address > 0xFFFF else self.hex_fmt(self.short_address, 4)

    def __repr__(self):
        return str(self)

    @classmethod
    def validate_bytes(cls, *args):
        """
        Values are passed in as positional arguments, low, high, and bank
        low value can be the entire 24 bit address if no other args are passed in
        high value can be the bank and high bytes if no bank byte is passed in
        bank value can only be 8 bits and values higher are truncated
        """
        low = args[0] if len(args) > 0 else 0x0
        high = args[1] if len(args) > 1 else 0x0
        bank = args[2] if len(args) > 2 else 0x0
        if low:
            low = cls.integer_or_hex(low, 0xFFFFFF)
            if low > 0xFFFF and not (high and bank):
                bank = SFCAddress.bank_byte(low)
                high = SFCAddress.high_byte(low)
                low = SFCAddress.low_byte(low)
            elif low > 0xFF and not high:
                high = SFCAddress.high_byte(low)
                low = SFCAddress.low_byte(low)
        if high:
            high = cls.integer_or_hex(high, 0xFFFF)
            if high > 0xFF and not bank:
                bank = SFCAddress.high_byte(high)
                high = SFCAddress.low_byte(high)
        if bank:
            bank = cls.integer_or_hex(bank)
        return low, high, bank

    @staticmethod
    def hex_fmt(value, pad=4, prefix='0x'):
        """
        Produce a formatted, hex value.
        default is padded with a prefix
        :param value: integer value to format as hex string
        :param pad: padded up to 4 characters by default
        :param prefix: prefix the hex string with any string -- '0x' by default
        """
        return f'{prefix}{value:0{pad}X}'

    @property
    def full_address(self):
        return (self.__full_pointer[0]) + (self.__full_pointer[1] * 0x100) + (self.__full_pointer[2] * 0x10000)

    @property
    def full_hex(self):
        return self.hex_fmt(self.full_address, 6)

    @property
    def short_address(self):
        return (self.__full_pointer[0]) + (self.__full_pointer[1] * 0x100)

    @property
    def short_hex(self):
        return self.hex_fmt(self.short_address)

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
        self.__full_pointer[0] = self.integer_or_hex(value)

    @property
    def high(self):
        return self.__full_pointer[1]

    @high.setter
    def high(self, value):
        self.__full_pointer[1] = self.integer_or_hex(value)

    @property
    def bank(self):
        return self.__full_pointer[2]

    @bank.setter
    def bank(self, value):
        self.__full_pointer[2] = self.integer_or_hex(value)

    def to_addr(self, addr_type):
        return SFCAddress(self.full_address, addr_type)

    @staticmethod
    def __check_list_tuple(value):
        if not (type(value) is list or type(value) is tuple):
            raise ValueError("Cannot assign any value but type of list or tuple.")
        if not len(value) >= 1:
            raise ValueError("List/Tuple length must be at least 1.")

    def __set_ptr_pos(self, index, input_val):
        if len(input_val) > index:
            self.__full_pointer[index] = self.integer_or_hex(input_val[index])

    @staticmethod
    def integer_or_hex(value: Union[int, str], mask: int = 0xFF) -> int:
        """
        validation for input values, also applies masking to the value
        :param value:
        :param mask: value is logical and'ed to the mask
        :return: normalized, and masked integer
        """
        if type(value) is str:
            if value.upper().startswith('0X'):
                value = value.replace('0X', '')
            try:
                value = int(value, 16)
            except ValueError as ex:
                raise ValueError('`address` parameter must be an integer or a hexadecimal string!')
        elif type(value) is not int:
            raise ValueError('`address` parameter must be an integer or a hexadecimal string!')
        return value & mask


class SFCAddressType:
    PC = 0
    LOROM1 = 1
    LOROM2 = 2
    HIROM = 3
    EXHIROM = 4
    EXLOROM = 5


class SFCAddress:
    def __init__(self, address: Union[int, str, list, tuple], address_type: int = SFCAddressType.PC,
                 default_value='N/A', hex_prefix='0x', decimal: bool = False, header: bool = False,
                 verbose=False, lorom_fallback=False):
        """
        Class can be instantiated in case multiple conversions are desired.
        :param address: integer/hexadecimal address value
        :param address_type: the input address type-- defaults to PC
        :param default_value: the value that is shown while printing values if the conversion failed
        :param hex_prefix: this string is prepended to the output hex value-- defaults to 0x (ex: 0x0BC018)
        :param decimal: boolean value to indicate the default conversion output value-- defaults to False
        :param header: indicate whether the conversion should take a copier header into account-- default False
        :param verbose: if more console output is desired-- default False
        :param lorom_fallback: if LoROM 1/2 conversion fails, they will fall back to the other type
        """
        self.__header = header
        self.__prefix = hex_prefix
        self.__show_hex = not decimal
        self.__default = default_value
        self.__verbose = verbose
        self.__lorom_fallback = lorom_fallback
        self.__initial_type = address_type

        if type(address) is not list and type(address) is not tuple:
            address = SFCPointer.integer_or_hex(address, 0xFFFFFF)
        else:
            ptr = SFCPointer(*address)
            address = ptr.full_address

        if address_type == SFCAddressType.PC:
            self.__address = address if not header else header - 512
        elif address_type == SFCAddressType.LOROM1:
            self.__address = self.lorom1_to_pc(address, self.__verbose, self.__lorom_fallback)
        elif address_type == SFCAddressType.LOROM2:
            self.__address = self.lorom2_to_pc(address, self.__verbose, self.__lorom_fallback)
        elif address_type == SFCAddressType.HIROM:
            self.__address = self.hirom_to_pc(address)
        elif address_type == SFCAddressType.EXHIROM:
            self.__address = self.exhirom_to_pc(address)
        elif address_type == SFCAddressType.EXLOROM:
            self.__address = self.exlorom_to_pc(address)
        else:
            raise ValueError('`address_type` parameter is invalid!')
        if verbose:
            print(self.all())

    def all(self):
        """
        Return a text representation of the current object using all possible conversions
        """
        hirom = self.hirom_address
        exhirom = self.exhirom_address
        lorom = self.lorom1_address
        exlorom = self.exlorom_address
        lorom2 = self.lorom2_address
        my_repr = f"=====TYPE====:=ADDRESS=\r\n****Binary/PC: {self.pc_address}"
        if lorom == exlorom:
            if lorom2 == lorom:
                my_repr += f"\r\n(1/2/Ex)LoROM: {lorom}"
            else:
                my_repr += f"\r\n**(1/Ex)LoROM: {lorom}\r\n*****(2)LoROM: {lorom2}"
        elif lorom2 == exlorom:
            my_repr += f"\r\n*****(1)LoROM: {lorom}\r\n**(2/Ex)LoROM: {lorom2}"
        else:
            my_repr += f"\r\n*****(1)LoROM: {lorom}\r\n*****(2)LoROM: {lorom2}\r\n******ExLoROM: {exlorom}"

        my_repr += f"\r\n*****Ex/HiROM: {hirom}" if hirom == exhirom else \
            f"\r\n********HiROM: {hirom}\r\n******ExHiROM: {exhirom}"

        return my_repr

    def __str__(self):
        return self.display_address(self.get_address(self.__initial_type))

    def __repr__(self):
        return f"{self.pc_address}({self.__address})"

    @lru_cache(0xFFFFFF)
    def display_address(self, addr, fill_hex_length=True, show_prefix=True):
        if addr is not None:
            if self.__show_hex:
                addr = hex(addr).upper().replace('0X', '')
                if fill_hex_length:
                    while len(addr) < 6:
                        addr = f"0{addr}"
                if show_prefix:
                    addr = f"{self.__prefix}{addr}"
            return addr
        return self.__default

    @lru_cache(0xFFFFFF)
    def get_address(self, address_type: Optional[int] = None) -> int:
        addr = 0
        if address_type is None:
            address_type = self.__initial_type

        if address_type == SFCAddressType.PC:
            addr = self.__address
        elif address_type == SFCAddressType.LOROM1:
            addr = self.pc_to_lorom1(self.__address)
        elif address_type == SFCAddressType.LOROM2:
            addr = self.pc_to_lorom2(self.__address)
        elif address_type == SFCAddressType.HIROM:
            addr = self.pc_to_hirom(self.__address)
        elif address_type == SFCAddressType.EXHIROM:
            addr = self.pc_to_exhirom(self.__address)
        elif address_type == SFCAddressType.EXLOROM:
            addr = self.pc_to_exlorom(self.__address)
        return addr

    def to_pointer(self, addr=None):
        if addr is None:
            addr = self.__address
        return SFCPointer(addr)

    @lru_cache(0xFFFFFF)
    def get_address_bytes(self, address_type: Optional[SFCAddressType] = None) -> list:
        return [self.get_low_byte(address_type), self.get_high_byte(address_type), self.get_bank_byte(address_type)]

    @lru_cache(0xFFFFFF)
    def get_low_byte(self, address_type: Optional[int] = None) -> int:
        addr = self.get_address(address_type)
        return self.low_byte(addr)

    @staticmethod
    @lru_cache(0xFFFFFF)
    def low_byte(addr: int):
        """
        Return a single (lowest) byte for a given address
        """
        return addr & 0xFF

    @lru_cache(0xFFFFFF)
    def get_high_byte(self, address_type: Optional[int] = None) -> int:
        addr = self.get_address(address_type)
        return self.high_byte(addr)

    @staticmethod
    @lru_cache(0xFFFFFF)
    def high_byte(addr: int):
        return int(addr / 0x100) & 0xFF

    @lru_cache(0xFFFFFF)
    def get_bank_byte(self, address_type: Optional[int] = None) -> int:
        addr = self.get_address(address_type)
        return self.bank_byte(addr)

    @staticmethod
    @lru_cache(0xFFFFFF)
    def bank_byte(addr: int):
        return int(addr / 0x10000) & 0xFF

    @property
    @lru_cache(0xFFFFFF)
    def pc_address(self):
        return self.display_address(self.__address if not self.__header else self.__address + 512)

    @property
    @lru_cache(0xFFFFFF)
    def lorom1_address(self):
        return self.display_address(self.pc_to_lorom1(self.__address))

    @property
    @lru_cache(0xFFFFFF)
    def lorom2_address(self):
        return self.display_address(self.pc_to_lorom2(self.__address))

    @property
    @lru_cache(0xFFFFFF)
    def exlorom_address(self):
        return self.display_address(self.pc_to_exlorom(self.__address))

    @property
    @lru_cache(0xFFFFFF)
    def hirom_address(self):
        return self.display_address(self.pc_to_hirom(self.__address))

    @property
    @lru_cache(0xFFFFFF)
    def exhirom_address(self):
        return self.display_address(self.pc_to_exhirom(self.__address))

    @classmethod
    @lru_cache(0xFFFFFF)
    def pc_to_lorom1(cls, pc_addr: int) -> Optional[int]:
        if pc_addr >= 0x400000:
            return None

        snes_addr = ((pc_addr << 1) & 0x7F0000) | ((pc_addr | 0x8000) & 0xFFFF)

        if pc_addr >= 0x380000:
            snes_addr += 0x800000

        return snes_addr

    @classmethod
    @lru_cache(0xFFFFFF)
    def pc_to_lorom2(cls, pc_addr: int) -> Optional[int]:
        if pc_addr >= 0x400000:
            return None

        return (((pc_addr << 1) & 0x7F0000) | ((pc_addr | 0x8000) & 0xFFFF)) + 0x800000

    @classmethod
    @lru_cache(0xFFFFFF)
    def pc_to_hirom(cls, pc_addr: int) -> Optional[int]:
        if pc_addr >= 0x400000:
            return None

        return pc_addr | 0xC00000

    @classmethod
    @lru_cache(0xFFFFFF)
    def pc_to_exlorom(cls, pc_addr: int) -> Optional[int]:
        if pc_addr >= 0x7F0000:
            return None

        snes_addr = ((pc_addr << 1) & 0x7F0000) | ((pc_addr | 0x8000) & 0xFFFF)

        if pc_addr < 0x400000:
            snes_addr += 0x800000

        return snes_addr

    @classmethod
    @lru_cache(0xFFFFFF)
    def pc_to_exhirom(cls, pc_addr: int) -> Optional[int]:
        if pc_addr >= 0x7E0000:
            return None

        snes_addr = pc_addr
        if pc_addr < 0x400000:
            snes_addr |= 0xC00000
        # elif pc_addr >= 0x7E0000:
        #     snes_addr -= 0x400000

        return snes_addr

    @classmethod
    @lru_cache(0xFFFFFF)
    def lorom1_to_pc(cls, snes_addr: int, verbose: bool = True, fallback=False) -> Optional[int]:
        if not (0x8000 <= snes_addr <= 0x6FFFFF):
            if verbose:
                print("Not a valid LoROM1 address!")
            return cls.lorom2_to_pc(snes_addr, verbose) if fallback else None

        return snes_addr & 0x7FFF | ((snes_addr & 0x7F0000) >> 1)

    @classmethod
    @lru_cache(0xFFFFFF)
    def lorom2_to_pc(cls, snes_addr: int, verbose: bool = True, fallback=False) -> Optional[int]:
        if not (0x808000 <= snes_addr <= 0xFFFFFF):
            if verbose:
                print("Not a valid LoROM2 address!")
            return cls.lorom1_to_pc(snes_addr, verbose) if fallback else None

        return snes_addr & 0x7FFF | ((snes_addr & 0x7F0000) >> 1)

    @classmethod
    @lru_cache(0xFFFFFF)
    def hirom_to_pc(cls, snes_addr: int) -> Optional[int]:
        if not (0xC00000 <= snes_addr <= 0xFFFFFF):
            print("Invalid HiROM Address!")
            return None

        return snes_addr & 0x3FFFFF

    @classmethod
    @lru_cache(0xFFFFFF)
    def exlorom_to_pc(cls, snes_addr: int) -> Optional[int]:
        if not ((0x808000 <= snes_addr <= 0xFFFFFF) or (0x008000 <= snes_addr <= 0x7DFFFF)):
            print("Invalid ExLoROM Address!")
            return None

        pc_addr = snes_addr & 0x7FFF | ((snes_addr & 0x7F0000) >> 1)

        if snes_addr < 0x800000:
            pc_addr += 0x400000

        return pc_addr

    @classmethod
    @lru_cache(0xFFFFFF)
    def exhirom_to_pc(cls, snes_addr: int) -> Optional[int]:
        if not ((0xC00000 <= snes_addr <= 0xFFFFFF) or (0x400000 <= snes_addr <= 0x7DFFFF)):
            print("Invalid ExHiROM Address!")
            return None

        pc_addr = snes_addr & 0x3FFFFF
        if snes_addr < 0xC00000:
            pc_addr += 0x400000

        return pc_addr


def lorom_to_hirom(in_data: list):
    """
    Converts a full binary rom file (list of bytes) from lorom to hirom format (doubles every bank)
    Quick and dirty.
    :param in_data: the original data
    :return: the hirom data
    """
    final_data = [0xFF] * (len(in_data) * 2)

    div = 0x8000
    pcs = int(len(in_data) / div)

    for c in range(0, pcs):
        for d in range(0, div):
            pc_pos = d + (c * div)
            hirom_pos = d + (c * 0x10000)
            final_data[hirom_pos] = 0xFF if c == 0 else in_data[pc_pos]
            final_data[hirom_pos + div] = in_data[pc_pos]
    return final_data


def run_test(function1, function2, i, name, verbose_progress, **kwargs):
    addr = function1(i)
    conv = function2(addr, **kwargs) if addr else 0
    if verbose_progress and addr:
        print(f"{hex(i)}->{hex(addr)}->{hex(conv) if conv is not None else 'ERR'} - {name}")
    if addr and not i == conv:
        print(f"{hex(i)}->{hex(addr)}->{hex(conv) if conv is not None else 'ERR'}"
              f" - {name} Back-Conversion Failed!")
        return False
    return True


def test_conv(start=0, end=0x7FFFFF, step=0x8000, verbose=True, stop_on_failure=True, lorom1_kwargs=None):
    fail_count = 0
    lorom1_kwargs = lorom1_kwargs if lorom1_kwargs else {'fallback': True, 'verbose': True}
    for i in range(start, end, step):
        if not run_test(SFCAddress.pc_to_lorom1, SFCAddress.lorom1_to_pc, i, "LOROM1", verbose,
                        **lorom1_kwargs):
            fail_count += 1

        if not run_test(SFCAddress.pc_to_lorom2, SFCAddress.lorom2_to_pc, i, "LOROM2", verbose):
            fail_count += 1

        if not run_test(SFCAddress.pc_to_hirom, SFCAddress.hirom_to_pc, i, "HIROM", verbose):
            fail_count += 1

        if not run_test(SFCAddress.pc_to_exlorom, SFCAddress.exlorom_to_pc, i, "EXLOROM", verbose):
            fail_count += 1

        if not run_test(SFCAddress.pc_to_exhirom, SFCAddress.exhirom_to_pc, i, "EXHIROM", verbose):
            fail_count += 1

        if stop_on_failure and fail_count > 0:
            break
        if verbose:
            print("...")

    if not fail_count:
        print("ALL TESTS PASSED!")
    else:
        print(f"ENCOUNTERED {fail_count} FAILURES!")

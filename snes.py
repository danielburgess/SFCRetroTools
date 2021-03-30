from typing import Optional, Union
from functools import lru_cache


class SFCAddressType:
    PC = 0
    LOROM1 = 1
    LOROM2 = 2
    HIROM = 3
    EXHIROM = 4
    EXLOROM = 5


class SFCAddressConvert:
    def __init__(self, address: Union[int, str], address_type: SFCAddressType = SFCAddressType.PC, default_value='N/A',
                 hex_prefix='0x', decimal: bool = False, header: bool = False, verbose=False, lorom_fallback=False):
        self.__header = header
        self.__prefix = hex_prefix
        self.__show_hex = not decimal
        self.__default = default_value
        self.__verbose = verbose
        self.__lorom_fallback = lorom_fallback

        if type(address) is str:
            if address.upper().startswith('0X'):
                address = address.replace('0X', '')
            try:
                address = int(address, 16)
            except ValueError as ex:
                raise ValueError('`address` parameter must be an integer or a hexadecimal string!')
        elif type(address) is not int:
            raise ValueError('`address` parameter must be an integer or a hexadecimal string!')

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
        print(self)

    def __str__(self):
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
    def get_address(self, address_type: Optional[SFCAddressType]) -> int:
        addr = 0
        if not address_type or address_type == SFCAddressType.PC:
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

    @lru_cache(0xFFFFFF)
    def get_address_bytes(self, address_type: Optional[SFCAddressType] = None) -> list:
        return [self.get_low_byte(address_type), self.get_high_byte(address_type), self.get_bank_byte(address_type)]

    @lru_cache(0xFFFFFF)
    def get_low_byte(self, address_type: Optional[SFCAddressType] = None) -> int:
        addr = self.get_address(address_type)
        return addr & 0xFF

    @lru_cache(0xFFFFFF)
    def get_high_byte(self, address_type: Optional[SFCAddressType] = None) -> int:
        addr = self.get_address(address_type)
        return (int(addr / 0x100) & 0x7F) + 0x80

    @lru_cache(0xFFFFFF)
    def get_bank_byte(self, address_type: Optional[SFCAddressType] = None) -> int:
        addr = self.get_address(address_type)
        return int(addr / 0x8000) & 0xFF

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
        if pc_addr >= 0x7E0000:
            snes_addr -= 0x400000

        return snes_addr

    @classmethod
    @lru_cache(0xFFFFFF)
    def lorom1_to_pc(cls, snes_addr: int, verbose: bool = True, fallback=False) -> Optional[int]:
        if not (0x8000 <= snes_addr <= 0x6FFFFF):
            if verbose:
                print("Not a valid LoROM1 address!")
            return cls.lorom2_to_pc(snes_addr) if fallback else None

        return snes_addr & 0x7FFF | ((snes_addr & 0x7F0000) >> 1)

    @classmethod
    @lru_cache(0xFFFFFF)
    def lorom2_to_pc(cls, snes_addr: int, verbose: bool = True, fallback=False) -> Optional[int]:
        if not (0x808000 <= snes_addr <= 0xFFFFFF):
            if verbose:
                print("Not a valid LoROM2 address!")
            return cls.lorom1_to_pc(snes_addr) if fallback else None

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
        if not run_test(SFCAddressConvert.pc_to_lorom1, SFCAddressConvert.lorom1_to_pc, i, "LOROM1", verbose,
                        **lorom1_kwargs):
            fail_count += 1

        if not run_test(SFCAddressConvert.pc_to_lorom2, SFCAddressConvert.lorom2_to_pc, i, "LOROM2", verbose):
            fail_count += 1

        if not run_test(SFCAddressConvert.pc_to_hirom, SFCAddressConvert.hirom_to_pc, i, "HIROM", verbose):
            fail_count += 1

        if not run_test(SFCAddressConvert.pc_to_exlorom, SFCAddressConvert.exlorom_to_pc, i, "EXLOROM", verbose):
            fail_count += 1

        if not run_test(SFCAddressConvert.pc_to_exhirom, SFCAddressConvert.exhirom_to_pc, i, "EXHIROM", verbose):
            fail_count += 1

        if stop_on_failure and fail_count > 0:
            break
        if verbose:
            print("...")

    if not fail_count:
        print("ALL TESTS PASSED!")
    else:
        print(f"ENCOUNTERED {fail_count} FAILURES!")

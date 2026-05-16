"""SFCAddress — SNES/SFC address conversion across mapping modes."""
from __future__ import annotations

from functools import lru_cache
from typing import Optional, Union

from retrotool.core.binary import (
    bank_byte as _bank_byte,
    high_byte as _high_byte,
    integer_or_hex,
    low_byte as _low_byte,
)


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
                 verbose=False, lorom_fallback=True):
        self.__header = header
        self.__prefix = hex_prefix
        self.__show_hex = not decimal
        self.__default = default_value
        self.__verbose = verbose
        self.__lorom_fallback = lorom_fallback
        self.__initial_type = address_type

        if not isinstance(address, (list, tuple)):
            address = integer_or_hex(address, 0xFFFFFF)
        else:
            from retrotool.core.pointer import SFCPointer
            address = SFCPointer(*address).full_address

        self.__given_address = address

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
        from retrotool.core.pointer import SFCPointer
        if addr is None:
            addr = self.__address
        return SFCPointer(addr)

    @lru_cache(0xFFFFFF)
    def get_address_bytes(self, address_type: Optional[int] = None) -> list:
        return [self.get_low_byte(address_type), self.get_high_byte(address_type), self.get_bank_byte(address_type)]

    @lru_cache(0xFFFFFF)
    def get_low_byte(self, address_type: Optional[int] = None) -> int:
        return _low_byte(self.get_address(address_type))

    low_byte = staticmethod(_low_byte)
    high_byte = staticmethod(_high_byte)
    bank_byte = staticmethod(_bank_byte)

    @lru_cache(0xFFFFFF)
    def get_high_byte(self, address_type: Optional[int] = None) -> int:
        return _high_byte(self.get_address(address_type))

    @lru_cache(0xFFFFFF)
    def get_bank_byte(self, address_type: Optional[int] = None) -> int:
        return _bank_byte(self.get_address(address_type))

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
    def pc_to_lorom1(cls, pc_addr: int, verbose: bool = False) -> Optional[int]:
        if pc_addr is None:
            if verbose:
                print("pc_to_lorom1: Given Address is invalid.")
            return None
        if pc_addr >= 0x400000:
            return None
        snes_addr = ((pc_addr << 1) & 0x7F0000) | ((pc_addr | 0x8000) & 0xFFFF)
        if pc_addr >= 0x380000:
            snes_addr += 0x800000
        return snes_addr

    @classmethod
    @lru_cache(0xFFFFFF)
    def pc_to_lorom2(cls, pc_addr: int, verbose: bool = False) -> Optional[int]:
        if pc_addr is None:
            if verbose:
                print("pc_to_lorom2: Given Address is invalid.")
            return None
        if pc_addr >= 0x400000:
            return None
        return (((pc_addr << 1) & 0x7F0000) | ((pc_addr | 0x8000) & 0xFFFF)) + 0x800000

    @classmethod
    @lru_cache(0xFFFFFF)
    def pc_to_hirom(cls, pc_addr: int, verbose: bool = False) -> Optional[int]:
        if pc_addr is None:
            if verbose:
                print("pc_to_hirom: Given Address is invalid.")
            return None
        if pc_addr >= 0x400000:
            return None
        return pc_addr | 0xC00000

    @classmethod
    @lru_cache(0xFFFFFF)
    def pc_to_exlorom(cls, pc_addr: int, verbose: bool = False) -> Optional[int]:
        if pc_addr is None:
            if verbose:
                print("pc_to_exlorom: Given Address is invalid.")
            return None
        if pc_addr >= 0x7F0000:
            return None
        snes_addr = ((pc_addr << 1) & 0x7F0000) | ((pc_addr | 0x8000) & 0xFFFF)
        if pc_addr < 0x400000:
            snes_addr += 0x800000
        return snes_addr

    @classmethod
    @lru_cache(0xFFFFFF)
    def pc_to_exhirom(cls, pc_addr: int, verbose: bool = False) -> Optional[int]:
        if pc_addr is None:
            if verbose:
                print("pc_to_exhirom: Given Address is invalid.")
            return None
        if pc_addr >= 0x7E0000:
            return None
        snes_addr = pc_addr
        if pc_addr < 0x400000:
            snes_addr |= 0xC00000
        return snes_addr

    @classmethod
    @lru_cache(0xFFFFFF)
    def lorom1_to_pc(cls, snes_addr: int, verbose: bool = True, fallback=False) -> Optional[int]:
        if snes_addr is None:
            if verbose:
                print("lorom1_to_pc: Given Address is invalid.")
            return None
        # LoROM1 window: banks $00–$6F, pages $8000–$FFFF. The lower bound is a
        # bank/page check, not a flat min; the previous `0x8000 <= x <= 0x6FFFFF`
        # was an empty interval (start > end) that always fell through.
        if snes_addr < 0x008000 or snes_addr > 0x6FFFFF or (snes_addr & 0xFFFF) < 0x8000:
            if verbose:
                print("Not a valid LoROM1 address!")
            return cls.lorom2_to_pc(snes_addr, verbose) if fallback else None
        return snes_addr & 0x7FFF | ((snes_addr & 0x7F0000) >> 1)

    @classmethod
    @lru_cache(0xFFFFFF)
    def lorom2_to_pc(cls, snes_addr: int, verbose: bool = True, fallback=False) -> Optional[int]:
        if snes_addr is None:
            if verbose:
                print("lorom2_to_pc: Given Address is invalid.")
            return None
        if not (0x808000 <= snes_addr <= 0xFFFFFF):
            if verbose:
                print("Not a valid LoROM2 address!")
            return cls.lorom1_to_pc(snes_addr, verbose) if fallback else None
        return snes_addr & 0x7FFF | ((snes_addr & 0x7F0000) >> 1)

    @classmethod
    @lru_cache(0xFFFFFF)
    def hirom_to_pc(cls, snes_addr: int, verbose: bool = False) -> Optional[int]:
        """Convert any valid HiROM SNES address (including mirror regions)
        to its ROM file PC offset.

        HiROM exposes ROM in four overlapping ranges, all of which map to
        the same PC bytes:
        - $C0-$FF:$0000-$FFFF — canonical full-bank ROM (4 MB total).
        - $40-$7D:$0000-$FFFF — mirror of $C0-$FF (slow-bus copy).
        - $00-$3F:$8000-$FFFF — upper-half mirror (lower half is WRAM/IO/SRAM).
        - $80-$BF:$8000-$FFFF — fast-rom upper-half mirror (lower half = same).

        Lower-half writes in $00-$3F / $80-$BF target WRAM/IO/SRAM, not
        ROM, so those are rejected — caller has either a bug or a non-ROM
        address. PC formula in every ROM region: `(bank & 0x3F) << 16 |
        offset` — the mask folds $C0-$FF / $80-$BF / $40-$7D / $00-$3F
        into a 0-63 PC bank (same ROM byte across all four mirror ranges).
        """
        if snes_addr is None:
            if verbose:
                print("hirom_to_pc: Given Address is invalid.")
            return None
        bank = (snes_addr >> 16) & 0xFF
        offset = snes_addr & 0xFFFF
        in_rom = (
            (0xC0 <= bank <= 0xFF)
            or (0x40 <= bank <= 0x7D)
            or (bank <= 0x3F and offset >= 0x8000)
            or (0x80 <= bank <= 0xBF and offset >= 0x8000)
        )
        if not in_rom:
            if verbose:
                print(f"Invalid HiROM Address: ${bank:02X}:{offset:04X}")
            return None
        return ((bank & 0x3F) << 16) | offset

    @classmethod
    @lru_cache(0xFFFFFF)
    def exlorom_to_pc(cls, snes_addr: int, verbose: bool = False) -> Optional[int]:
        if snes_addr is None:
            if verbose:
                print("exlorom_to_pc: Given Address is invalid.")
            return None
        if not ((0x808000 <= snes_addr <= 0xFFFFFF) or (0x008000 <= snes_addr <= 0x7DFFFF)):
            print("Invalid ExLoROM Address!")
            return None
        pc_addr = snes_addr & 0x7FFF | ((snes_addr & 0x7F0000) >> 1)
        if snes_addr < 0x800000:
            pc_addr += 0x400000
        return pc_addr

    @classmethod
    @lru_cache(0xFFFFFF)
    def exhirom_to_pc(cls, snes_addr: int, verbose: bool = False) -> Optional[int]:
        if snes_addr is None:
            if verbose:
                print("exhirom_to_pc: Given Address is invalid.")
            return None
        if not ((0xC00000 <= snes_addr <= 0xFFFFFF) or (0x400000 <= snes_addr <= 0x7DFFFF)):
            print("Invalid ExHiROM Address!")
            return None
        pc_addr = snes_addr & 0x3FFFFF
        if snes_addr < 0xC00000:
            pc_addr += 0x400000
        return pc_addr

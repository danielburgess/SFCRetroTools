"""Backward-compat shim. Import from retrotool.core instead."""
from retrotool.core.address import SFCAddress, SFCAddressType
from retrotool.core.pointer import SFCPointer
from retrotool.core.rom import lorom_to_hirom

__all__ = ["SFCAddress", "SFCAddressType", "SFCPointer", "lorom_to_hirom"]

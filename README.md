# SFCRetroTools
#### ===ROM Hacking Tools for the Super Famicom===

This repo will contain various libraries I've created for my personal use.

Currently, this only contains an Address Conversion tool, and a Pointer class which are used similarly to LunarAddress, except I don't support ZSNES save states. I could, but... does anyone even use ZSNES anymore?

### Supported Address Mapping Conversions:
* LoROM (Type 1/2)
* HiROM
* ExLoROM
* ExHiROM
* PC/Binary

### Basic Usage:

```python
from retrotool.snes import SFCAddress, SFCAddressType

addr = SFCAddress(0x5f800, SFCAddressType.PC)
print(addr.all())  # all applicable conversions are shown
print(addr.exhirom_address)  # hex-formatted EXHIROM address
```
```text
=====TYPE====:=ADDRESS=
****Binary/PC: 0x05F800
*****(1)LoROM: 0x0BF800
**(2/Ex)LoROM: 0x8BF800
*****Ex/HiROM: 0xC5F800
'0xC5F800'
```

## Short Explainer
There are several helper methods that can be used for converting SNES/SFC addresses. Essentially, this library can be combined with any number of other tools such as script dumping, pointer table generation, address conversions built in to hex editors, etc.

=======
I plan on adding more tools as I need it, but in the meantime, 

---

# SFCPointer Class

## Description
The `SFCPointer` class represents a Super Famicom (SFC) pointer. Pointers can be defined, modified, and read in various ways. The class allows you to specify low, high, and bank values, and it provides methods for validation, conversion, and display.

## Constructor

### `init(self, low=None, high=None, bank=None)`

Pointers can be defined, modified, and read in many ways. Low and high values can be used to fill part of, or the entire address, depending on what is desired.

- `low`: Can be as small as 8 bit, as big as 24 bit (over 24 bit is ignored).
- `high`: Can be 8 to 16 bit (over 16 bit is ignored).
- `bank`: Only be 8 bit (extra data is lost).

## Methods

### `validate_bytes(cls, *args)`
Validates and normalizes low, high, and bank values.
- `args`: Low, high, and bank values.
Returns a tuple `(low, high, bank)`.

### `hex_fmt(value, pad=4, prefix='0x')`
Formats an integer value as a hexadecimal string.
- `value`: Integer value to be formatted.
- `pad`: Width of the formatted string (default is 4).
- `prefix`: Prefix for the hexadecimal string (default is '0x').
Returns the formatted hexadecimal string.

### `to_addr(addr_type)`
Converts the `SFCPointer` to an `SFCAddress` instance.
- `addr_type`: The address type to convert to.
Returns an `SFCAddress` instance.

### `integer_or_hex(value: Union[int, str], mask: int = 0xFF) -> int`
Validates and normalizes input values, applying masking to the value.
- `value`: Input value (integer or hexadecimal string).
- `mask`: Mask to be applied (default is 0xFF).
Returns the normalized and masked integer value.

### `__set_ptr_pos(self, index, input_val)`
Sets the value at the specified index in the full pointer.

---

# SFCAddressType Class

## Description
The `SFCAddressType` class defines constants representing different Super Famicom (SFC) address types.

## Constants
- `PC`: Address type for PC addresses.
- `LOROM1`: Address type for LoROM1 addresses.
- `LOROM2`: Address type for LoROM2 addresses.
- `HIROM`: Address type for HiROM addresses.
- `EXHIROM`: Address type for ExHiROM addresses.
- `EXLOROM`: Address type for ExLoROM addresses.

---

# SFCAddress Class

## Description
The `SFCAddress` class provides a flexible way to handle Super Famicom (SFC) addresses. It allows for instantiation with various input types and supports multiple conversions between different address types.

## Constructor

```python
__init__(self, address: Union[int, str, list, tuple], address_type: int = SFCAddressType.PC,
         default_value='N/A', hex_prefix='0x', decimal: bool = False, header: bool = False,
         verbose=False, lorom_fallback=True) 
```
         
- `address`: Integer, hexadecimal string, list, or tuple representing the address value.
- `address_type`: The input address type (default is SFCAddressType.PC).
- `default_value`: The value shown while printing if the conversion fails (default is 'N/A').
- `hex_prefix`: String prepended to the output hex value (default is '0x').
- `decimal`: Boolean indicating the default conversion output value (default is False).
- `header`: Indicates whether the conversion should consider a copier header (default is False).
- `verbose`: If more console output is desired (default is False).
- `lorom_fallback`: If LoROM 1/2 conversion fails, it will fall back to the other type.

## Properties and Methods

### `all(self) -> str`
Prints a formatted representation of the address in various SFC address types.

### `display_address(self, addr, fill_hex_length=True, show_prefix=True) -> Union[str, int]`
Formats and displays the given address.

### `get_address(self, address_type: Optional[int] = None) -> int`
Returns the address in the specified type.

### `to_pointer(self, addr=None) -> SFCPointer`
Converts the given address to an SFCPointer object.

### `get_address_bytes(self, address_type: Optional[SFCAddressType] = None) -> list`
Returns a list of low, high, and bank bytes of the address.

### `get_low_byte(self, address_type: Optional[int] = None) -> int`
Returns the low byte of the address.

### `get_high_byte(self, address_type: Optional[int] = None) -> int`
Returns the high byte of the address.

### `get_bank_byte(self, address_type: Optional[int] = None) -> int`
Returns the bank byte of the address.

### `pc_address(self) -> str`
Returns the address in PC format.

### `lorom1_address(self) -> str`
Returns the address in LoROM1 format.

### `lorom2_address(self) -> str`
Returns the address in LoROM2 format.

### `exlorom_address(self) -> str`
Returns the address in ExLoROM format.

### `hirom_address(self) -> str`
Returns the address in HiROM format.

### `exhirom_address(self) -> str`
Returns the address in ExHiROM format.

## Class Methods

### `pc_to_lorom1(cls, pc_addr: int, verbose: bool = False) -> Optional[int]`
Converts a PC address to LoROM1 format.

### `pc_to_lorom2(cls, pc_addr: int, verbose: bool = False) -> Optional[int]`
Converts a PC address to LoROM2 format.

### `pc_to_hirom(cls, pc_addr: int, verbose: bool = False) -> Optional[int]`
Converts a PC address to HiROM format.

### `pc_to_exlorom(cls, pc_addr: int, verbose: bool = False) -> Optional[int]`
Converts a PC address to ExLoROM format.

### `pc_to_exhirom(cls, pc_addr: int, verbose: bool = False) -> Optional[int]`
Converts a PC address to ExHiROM format.

### `lorom1_to_pc(cls, snes_addr: int, verbose: bool = True, fallback=False) -> Optional[int]`
Converts a LoROM1 address to PC format.

### `lorom2_to_pc(cls, snes_addr: int, verbose: bool = True, fallback=False) -> Optional[int]`
Converts a LoROM2 address to PC format.

### `hirom_to_pc(cls, snes_addr: int1, verbose: bool = False) -> Optional[int]`
Converts a HiROM address to PC format.

### `exlorom_to_pc(cls, snes_addr: int, verbose: bool = False) -> Optional[int]`
Converts an ExLoROM address to PC format.

### `exhirom_to_pc(cls, snes_addr: int, verbose: bool = False) -> Optional[int]`
Converts an ExHiROM address to PC format.


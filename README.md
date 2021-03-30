# RetroTool
#### ===ROM Hacking Tools===

This repo will contain various libraries I've created for my personal use.

Currently, this only contains an Address Conversion tool, and a Pointer class which are used similarly to LunarAddress, except I don't support ZSNES save states. (I could, but... meh.)

### Supported Address Mapping Conversions:
* LoROM (Type 1/2)
* HiROM
* ExLoROM
* ExHiROM
* PC/Binary

### Basic Usage:
```python
from snes import SFCAddressConvert, SFCAddressType
converter = SFCAddressConvert(0x5f800, SFCAddressType.PC)
str(converter)
print(converter.exhirom_address)
```
```text
=====TYPE====:=ADDRESS=
****Binary/PC: 0x05F800
*****(1)LoROM: 0x0BF800
**(2/Ex)LoROM: 0x8BF800
*****Ex/HiROM: 0xC5F800
'0xC5F800'
```
```python
from snes import SFCPointer
ptr_obj = SFCPointer(0x5f800)
print(ptr_obj)
print(ptr_obj.short_hex)
print(ptr_obj.short_address)
print(ptr_obj.full_address)
```
```text
'0x0BF800'
'0xF800'
63488
784384
```

## Short Explainer
There are several helper methods that can be used for converting SNES/SFC addresses. Essentially, this library can be combined with any number of other tools such as script dumping, pointer table generation, address conversions built in to hex editors, etc.

I plan on adding more tools as I need it, but in the meantime, hopefully somebody can make use of this.
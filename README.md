# RetroTool
#### ===ROM Hacking Tools===

This repo will contain various libraries I've created for my personal use.

Currently, this only contains an Address Conversion tool which is similar in use to LunarAddress, except I don't support ZSNES save states. (I could, but would rather not.)

###Supported Address Mapping Conversions:
* LoROM (Type 1/2)
* HiROM
* ExLoROM
* ExHiROM
* PC/Binary

###Basic Usage:
```python
from snes import SFCAddressConvert, SFCAddressType
converter = SFCAddressConvert(0x5f800, SFCAddressType.PC)
str(converter)
```
```text
=====TYPE====:=ADDRESS=
****Binary/PC: 0x05F800
*****(1)LoROM: 0x0BF800
**(2/Ex)LoROM: 0x8BF800
*****Ex/HiROM: 0xC5F800
```
```python
print(converter.exhirom_address)
```
```text
0xC5F800
```

##Short Explainer
There are several helper methods that can be used for converting SNES/SFC addresses. Essentially, this library can be combined with any number of other tools such as script dumping, pointer table generation, address conversions built in to hex editors, etc.

I plan on adding more tools as I need it, but in the meantime, hopefully somebody can make use of this.
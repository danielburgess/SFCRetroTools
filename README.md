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
from snes import SFCAddress, SFCAddressType

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

I plan on adding more tools as I need it, but in the meantime, hopefully somebody can make use of this.
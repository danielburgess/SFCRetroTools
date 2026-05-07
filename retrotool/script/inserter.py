"""Compile text strings back to bytes + rebuild pointer table."""
from __future__ import annotations

from dataclasses import dataclass, field

from retrotool.core.address import SFCAddress, SFCAddressType
from retrotool.core.binary import write_u16_le, write_u24_le
from retrotool.project.datadef import DataDef
from retrotool.script.table import Table


@dataclass
class InsertedScript:
    pointer_table: bytes        # bytes to write to pointer table location
    data_block: bytes           # contiguous encoded string data
    data_base: int              # SNES address of first string in data_block
    entry_offsets: list[int] = field(default_factory=list)  # PC offset into data_block per entry


def compile_script(
    texts: list[str],
    datadef: DataDef,
    table: Table,
    address_type: int = SFCAddressType.LOROM1,
    data_base: int | None = None,
) -> InsertedScript:
    """Encode each string, concatenate, emit pointer table referencing each offset."""
    if datadef.pointers is None:
        raise ValueError(f"datadef {datadef.name}: [pointers] required")

    ptrs = datadef.pointers
    if len(texts) != ptrs.count:
        raise ValueError(f"{datadef.name}: expected {ptrs.count} strings, got {len(texts)}")

    terminator = datadef.encoding.terminator if datadef.encoding else 0x00
    target_snes = (datadef.relocation.target if datadef.relocation else
                   (data_base if data_base is not None else (datadef.data.offset if datadef.data else 0)))
    pointer_size = datadef.relocation.pointer_size if datadef.relocation else ptrs.size

    data = bytearray()
    offsets: list[int] = []
    for text in texts:
        offsets.append(len(data))
        data.extend(table.encode_text(text))
        data.append(terminator)

    ptr_bytes = bytearray()
    for off in offsets:
        addr_pc = SFCAddress(target_snes, address_type).get_address(SFCAddressType.PC) + off
        addr_snes = SFCAddress(addr_pc, SFCAddressType.PC).get_address(address_type)
        if pointer_size == 2:
            ptr_bytes.extend(write_u16_le(addr_snes & 0xFFFF))
        else:
            ptr_bytes.extend(write_u24_le(addr_snes & 0xFFFFFF))

    return InsertedScript(
        pointer_table=bytes(ptr_bytes),
        data_block=bytes(data),
        data_base=target_snes,
        entry_offsets=offsets,
    )

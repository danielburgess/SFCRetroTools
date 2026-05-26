"""Extract scripts from ROM using a DataDef + Table."""
from __future__ import annotations

from dataclasses import dataclass, field

from retrotool.core.address import SFCAddress, SFCAddressType
from retrotool.core.binary import read_u16_le, read_u24_le
from retrotool.project.datadef import DataDef
from retrotool.script.table import Table


@dataclass
class ScriptEntry:
    id: str
    pointer_addr: int       # PC offset of the pointer itself
    data_addr: int          # PC offset of the string data
    raw: bytes              # raw bytes up to (and including) terminator
    text: str = ""          # decoded text


@dataclass
class Script:
    name: str
    entries: list[ScriptEntry] = field(default_factory=list)


def extract_script(
    rom: bytes,
    datadef: DataDef,
    table: Table,
    address_type: int = SFCAddressType.LOROM1,
) -> Script:
    """Read pointer table from ROM + decode each string through Table."""
    if datadef.pointers is None or datadef.data is None:
        raise ValueError(f"datadef {datadef.name}: requires [pointers] + [data] to extract")

    ptrs = datadef.pointers
    terminator = datadef.encoding.terminator if datadef.encoding else 0x00

    ptr_pc = _to_pc(ptrs.offset, address_type)
    data_start_pc = _to_pc(datadef.data.offset, address_type)
    data_end_pc = _to_pc(datadef.data.end, address_type) if datadef.data.end else len(rom)

    # When the table declares any @ctrl_prefix bytes (single- or multi-),
    # defer to Table.find_entry_end — it walks each prefix's per-cmd length
    # so parameter bytes equal to `terminator` don't prematurely cut the
    # entry off. Tables without any @ctrl_prefix declaration keep the
    # simple byte-walk via _read_until.
    #
    # Gating on `ctrl_prefixes` (not `ctrl_lengths`) is needed for
    # multi-prefix tables that declare prefix-default lengths but no
    # per-cmd overrides — those have empty per-prefix `cmds` dicts and
    # would falsely report `bool(ctrl_lengths) == False`.
    use_ctrl_walk = bool(getattr(table, "ctrl_prefixes", None))

    entries: list[ScriptEntry] = []
    for i in range(ptrs.count):
        p_off = ptr_pc + i * ptrs.size
        if ptrs.size == 2:
            rel = read_u16_le(rom, p_off)
            bank = ptrs.bank_override or SFCAddress.bank_byte(datadef.data.offset)
            snes = (bank << 16) | rel
        else:
            snes = read_u24_le(rom, p_off)
        data_pc = _to_pc(snes, address_type)
        if data_pc < data_start_pc or data_pc >= data_end_pc:
            continue
        if use_ctrl_walk:
            end_pc = table.find_entry_end(
                rom, data_pc, max_addr=data_end_pc, terminator=terminator
            )
            raw = bytes(rom[data_pc:end_pc])
        else:
            raw = _read_until(rom, data_pc, terminator, data_end_pc)
        text = table.interpret_binary_data(list(raw), max_bytes=3, trim_bytes=[terminator])
        entries.append(ScriptEntry(
            id=f"{datadef.name}[{i:04d}]",
            pointer_addr=p_off,
            data_addr=data_pc,
            raw=raw,
            text=text,
        ))
    return Script(name=datadef.name, entries=entries)


def _to_pc(snes_or_pc: int, address_type: int) -> int:
    pc = SFCAddress(snes_or_pc, address_type).get_address(SFCAddressType.PC)
    if pc is None:
        raise ValueError(f"Invalid address {snes_or_pc:#08X} for mapping type {address_type}")
    return pc


def _read_until(rom: bytes, start: int, terminator: int, limit: int) -> bytes:
    end = start
    while end < limit and rom[end] != terminator:
        end += 1
    if end < limit:
        end += 1                 # include terminator
    return bytes(rom[start:end])

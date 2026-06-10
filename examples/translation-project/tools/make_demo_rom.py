#!/usr/bin/env python3
"""make_demo_rom.py — synthesize the demo "game" ROM this example translates.

Real translation projects start from a legally-obtained copy of a game that
can never be distributed. So that this example is runnable end-to-end out of
the box, this script *generates* a stand-in: a minimal valid 512 KB LoROM
image containing a Japanese dialog pointer table — the same shape a real
game's script engine uses, and exactly what `defs/dialog.toml` describes:

  PC 0x4000   pointer table: 3 entries x 2-byte little-endian pointers
              (within-bank LoROM addresses: (pc & 0x7FFF) | 0x8000)
  PC 0x4040   the dialog strings, one after another, each ending in the
              0xFF terminator. Kana are single bytes per tables/game_jp.tbl
              (0x80=こ 0x81=ん ...), 0xFD is the engine's line-break code.

Stdlib only. Usage (from the example root):

    python tools/make_demo_rom.py        # writes roms/game.sfc

Then follow README.md: `retrotool build .` inserts the English script over
this ROM, and `retrotool extract . --lang jp` can re-derive data/jp/ from it.
"""
from __future__ import annotations

from pathlib import Path

ROM_SIZE = 0x80_000          # 512 KB
PTR_TABLE_PC = 0x4000        # where the game keeps its dialog pointer table
DATA_PC = 0x4040             # where the original strings live
TERMINATOR = 0xFF
NEWLINE = 0xFD               # the demo engine's explicit line-break opcode

# Byte values per tables/game_jp.tbl — keep the two in sync.
KANA = {
    "こ": 0x80, "ん": 0x81, "に": 0x82, "ち": 0x83, "は": 0x84,
    "せ": 0x85, "か": 0x86, "い": 0x87, "た": 0x88, "え": 0x89,
    "わ": 0x8A, "り": 0x8B, "お": 0x8C, "!": 0x21,
}

# The demo game's entire script: list of lines per entry; lines are joined
# with the NEWLINE control byte, entries end with TERMINATOR.
SCRIPT = [
    ["こんにちは", "せかい!"],     # 0: "Hello, world!"
    ["たたかえ!"],                 # 1: "Fight!"
    ["おわり"],                    # 2: "The End"
]


def encode_entry(lines: list[str]) -> bytes:
    out = bytearray()
    for i, line in enumerate(lines):
        if i:
            out.append(NEWLINE)
        for ch in line:
            out.append(KANA[ch])
    out.append(TERMINATOR)
    return bytes(out)


def pc_to_lorom_word(pc: int) -> int:
    """16-bit within-bank LoROM pointer for a PC offset (bank implied by the
    pointer table's own bank — how most 16-bit script pointers work)."""
    return (pc & 0x7FFF) | 0x8000


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    body = bytearray(ROM_SIZE)

    # Strings first, recording where each lands.
    pc = DATA_PC
    pointers: list[int] = []
    for entry in SCRIPT:
        blob = encode_entry(entry)
        pointers.append(pc_to_lorom_word(pc))
        body[pc:pc + len(blob)] = blob
        pc += len(blob)

    # Pointer table.
    for i, ptr in enumerate(pointers):
        off = PTR_TABLE_PC + i * 2
        body[off:off + 2] = ptr.to_bytes(2, "little")

    # Minimal valid LoROM internal header at 0x7FC0 + checksum.
    body[0x7FC0:0x7FC0 + 21] = b"RETROTOOL DEMO       "
    body[0x7FD5] = 0x20      # LoROM, slow
    body[0x7FD6] = 0x00      # chipset: ROM only
    body[0x7FD7] = 0x09      # 512 KB
    body[0x7FD8] = 0x00      # no SRAM
    body[0x7FD9] = 0x00      # region: Japan
    body[0x7FDA] = 0x33
    body[0x7FDB] = 0x00
    body[0x7FDC:0x7FE0] = b"\xFF\xFF\x00\x00"
    csum = sum(body) & 0xFFFF
    body[0x7FDC] = (csum ^ 0xFFFF) & 0xFF
    body[0x7FDD] = ((csum ^ 0xFFFF) >> 8) & 0xFF
    body[0x7FDE] = csum & 0xFF
    body[0x7FDF] = (csum >> 8) & 0xFF

    out = root / "roms" / "game.sfc"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(body)
    print(f"wrote {out} ({ROM_SIZE // 1024} KB, "
          f"{len(SCRIPT)} dialog entries at 0x{PTR_TABLE_PC:X})")


if __name__ == "__main__":
    main()

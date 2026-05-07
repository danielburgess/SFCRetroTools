"""`str.format`-style prompt templates for RE / ROM-hacking workflows.

Consumed by external LLM-driven scripts; this module sends nothing on its own.
"""
from __future__ import annotations

IDENTIFY_COMPRESSION = """\
You are analyzing a suspected compressed block from a SNES ROM.

Block starts at ROM offset {offset:#x} ({offset} decimal).
First 64 bytes (hex): {head}

The game is known to use an LZSS variant. Your task:
1. Propose the most likely LZSSParams (window_bits, fill_byte, init_pos, bit_order).
2. Explain which bytes in the header/stream support your guess.
3. Suggest a trial decompression and what its first ~32 output bytes should look like.

Respond as JSON with keys: params, rationale, trial_plan.
"""

LOCATE_TEXT_TABLE = """\
Help locate a character-encoding table in a SNES ROM.

Known clues:
- Region of interest: {region_start:#x}..{region_end:#x}
- Sample pointer-table entries: {sample_pointers}
- Printable-ASCII fraction in {region_start:#x}: {printable_ratio:.2f}

Return: a hypothesis for where the table lives, what base value maps to 'A', and a plan
to confirm (e.g., breakpoint on glyph renderer, observe A-index correspondence).
"""

DISCOVER_LEVEL_FORMAT = """\
Reverse-engineer the level format.

Observed:
- Level pointer table at {level_ptrs:#x} with {count} entries.
- First entry resolves to {first_level:#x}.
- Disassembly near the level-load routine: {disasm_excerpt}

Produce a hypothesis for the level header structure (field layout in bytes),
the tile data format (raw vs RLE vs LZSS), and the collision-map location.
"""

SUGGEST_ASAR_HOOK = """\
Generate an Asar patch that hooks the routine at ${addr:06X} and jumps to free space
in bank ${freespace_bank:02X} for extended logic.

Target behavior: {behavior}
Original bytes at hook site: {original_bytes}

Produce valid Asar assembly. Preserve the original routine's semantics unless the
behavior requires otherwise.
"""

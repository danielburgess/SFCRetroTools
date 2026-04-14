"""Common 65816 ASM patch templates."""
from __future__ import annotations


def hook_jsl(label: str, target: str) -> str:
    """JSL hook at `label` to `target`."""
    return f"org ${label}\n    JSL {target}\n"


def redirect_pointer_table(org_addr: str, new_addr: str) -> str:
    return f"org ${org_addr}\n    dl ${new_addr}\n"


def freespace_block(start: str, body: str) -> str:
    return f"freespace ${start}\n{body}\n"


def data_block(label: str, data: bytes) -> str:
    hex_bytes = ', '.join(f'${b:02X}' for b in data)
    return f"{label}:\n    db {hex_bytes}\n"

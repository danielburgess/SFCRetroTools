"""retrotool.asm — ASM patching + codegen."""
from retrotool.asm.codegen import AsmBuilder, AsmLine
from retrotool.asm.freespace import Allocation, FreeRegion, FreeSpace
from retrotool.asm.patcher import AsarPatch, PatchResult, apply_patch
from retrotool.asm.templates import data_block, freespace_block, hook_jsl, redirect_pointer_table

__all__ = [
    "AsmBuilder",
    "AsmLine",
    "FreeSpace",
    "FreeRegion",
    "Allocation",
    "AsarPatch",
    "PatchResult",
    "apply_patch",
    "hook_jsl",
    "redirect_pointer_table",
    "freespace_block",
    "data_block",
]

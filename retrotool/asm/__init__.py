"""retrotool.asm — ASM patching + codegen + ca65/ld65 wrappers."""
from retrotool.asm.ca65 import (
    AsmResult,
    Ca65Assembler,
    Ca65Error,
    Ld65Error,
    Ld65Linker,
    LinkResult,
)
from retrotool.asm.codegen import AsmBuilder, AsmLine
from retrotool.asm.libsfx import (
    BuildResult,
    LibSFXConfig,
    LibSFXProject,
    scaffold_libsfx_project,
)
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
    "Ca65Assembler",
    "Ca65Error",
    "Ld65Linker",
    "Ld65Error",
    "AsmResult",
    "LinkResult",
    "LibSFXConfig",
    "LibSFXProject",
    "BuildResult",
    "scaffold_libsfx_project",
]

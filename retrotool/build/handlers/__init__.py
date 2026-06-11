"""Per-`SectionKind` build handlers.

Each handler takes a mutable bytearray (the working ROM body, SMC header
stripped) and a `Section`, and writes its bytes. Handlers return the
(offset, length) range(s) they wrote — the caller uses this to grow the
buffer if needed and to summarize the build.

Split by concern (this package was a single 2,700-line module):
  _base      — shared types (BuildContext, HandlerError, WriteRange),
               ROM-write/path/attr helpers, pointer encoders
  basic      — rep / ins / bin / graphics
  assembler  — asar / ca65 / bass
  meta       — python / project / libsfx
  script     — script + windowed-script (relocate & overflow modes)
  records    — fixed-records

The import surface is unchanged: everything importable from the old
`retrotool.build.handlers` module resolves here.
"""
from __future__ import annotations

from pathlib import Path  # noqa: F401 — part of the historical surface
from typing import Optional

from retrotool.build.spec import Section, SectionKind  # noqa: F401

from retrotool.build.handlers._base import (  # noqa: F401
    BuildContext,
    HandlerError,
    HandlerFn,
    WriteRange,
    _PreparedScript,
    _attr_hex,
    _diff_ranges,
    _ensure_room,
    _load_callable,
    _parse_pipe_kvs,
    _pc_to_hirom_bytes,
    _pc_to_hirom_within_bank,
    _pc_to_lorom0_bytes,
    _pc_to_lorom1_bytes,
    _pc_to_lorom_within_bank,
    _read_concat,
    _resolve,
    _resolve_pointer_table_pc,
    _run_callable,
    _select_16bit_within_bank,
    _select_24bit_encoder,
    _wrap_assembler_writes,
    _write,
)
from retrotool.build.handlers.assembler import (  # noqa: F401
    handle_asar,
    handle_bass,
    handle_ca65,
)
from retrotool.build.handlers.basic import (  # noqa: F401
    _handle_graphics_png,
    _is_identity_bitplane,
    bitplane_reverse,
    handle_bin,
    handle_graphics,
    handle_ins,
    handle_rep,
)
from retrotool.build.handlers.meta import (  # noqa: F401
    handle_libsfx,
    handle_project,
    handle_python,
)
from retrotool.build.handlers.records import (  # noqa: F401
    _apply_field_ptr_writes,
    _coerce_file_offset,
    _looks_like_fixed_script,
    _pack_fixed_records,
    handle_fixed_records,
)
from retrotool.build.handlers.script import (  # noqa: F401
    _emit_auto_window_writes,
    _emit_windowed_marker_writes,
    _handle_script_windowed,
    _script_placement_mode,
    _script_prepare_overflow,
    _script_prepare_relocate,
    handle_script,
    script_prepare,
)


HANDLERS: dict[SectionKind, HandlerFn] = {
    SectionKind.REP: handle_rep,
    SectionKind.INS: handle_ins,
    SectionKind.BIN: handle_bin,
    SectionKind.GRAPHICS: handle_graphics,
    SectionKind.SCRIPT: handle_script,
    SectionKind.ASAR: handle_asar,
    SectionKind.BASS: handle_bass,
    SectionKind.CA65: handle_ca65,
    SectionKind.PROJECT: handle_project,
    SectionKind.FIXED_RECORDS: handle_fixed_records,
    SectionKind.LIBSFX: handle_libsfx,
    SectionKind.PYTHON: handle_python,
    # Back-compat alias: `kind="windowed-script"` routes to the unified
    # script handler. `placement.mode = "overflow"` on `kind="script"` is
    # the preferred form; windowed-script is deprecated and kept so existing
    # TOML specs keep building.
    SectionKind.WINDOWED_SCRIPT: handle_script,
}


def get_handler(kind: SectionKind) -> Optional[HandlerFn]:
    return HANDLERS.get(kind)

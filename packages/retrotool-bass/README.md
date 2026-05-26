# retrotool-bass

Bundled [bass v18 (ARM9 fork)](https://github.com/ARM9/bass) multi-architecture assembler CLI for retrotool. Installing this wheel makes `bass` available to `retrotool.asm.patcher` without a system bass on PATH.

bass is a table-based, multi-architecture, cross-platform macro assembler. Originally by byuu; the v18 ARM9 fork is the actively-maintained branch retrotool builds against.

## Usage

```python
from retrotool_bass import bass_binary, run_bass

# bass v18 modify-mode invocation: -m <target> applies the patch in place.
run_bass(["-m", "rom.sfc", "patch.asm"])
```

`retrotool.asm.patcher.apply_bass_patch` prefers the bundled binary when this package is installed, else falls back to `bass` on PATH.

## Architecture files

bass loads architecture definitions (`snes.cpu.arch`, `n64.cpu.arch`, etc.) at runtime. The wheel bundles the `architectures/` directory next to the binary so bass's `Path::program()` lookup succeeds without any extra setup. Override at runtime by pointing your script at a different `bass` build, or by placing custom `.arch` files under `~/.local/share/bass/architectures/` (bass searches user-data first).

## License

bass v18 (ARM9 fork) is ISC. This wheel redistributes the compiled binary and architecture data under the same license. See `vendor/bass/README.md` for upstream attribution and `vendor/bass/LICENSE` if present.

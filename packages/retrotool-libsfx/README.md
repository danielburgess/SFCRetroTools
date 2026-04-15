# retrotool-libsfx

Bundled [Optiroc](https://github.com/Optiroc) SNES toolchain for the `retrotool`
build pipeline. One wheel, one install, whole stack.

## What's inside

Built from vendored upstream sources (git submodules) during wheel construction:

| Tool | Upstream | Purpose |
|------|----------|---------|
| [libSFX](https://github.com/Optiroc/libSFX) | `vendor/libSFX` | 65816 asm framework (headers + macros, no binary) |
| [SuperFamiconv](https://github.com/Optiroc/SuperFamiconv) | `vendor/SuperFamiconv` | PNG → tiles / palette / tilemap converter |
| [SuperFamicheck](https://github.com/Optiroc/SuperFamicheck) | `vendor/SuperFamicheck` | SNES header + checksum fixer |
| [BRRtools](https://github.com/Optiroc/BRRtools) | `vendor/BRRtools` | BRR audio encode/decode |
| [lz4](https://github.com/lz4/lz4) | `vendor/lz4` | reference LZ4 compressor |
| [cc65](https://github.com/cc65/cc65) | `vendor/cc65` | ca65/ld65 assembler + linker for libSFX projects |
| [make_breakpoints](https://github.com/Optiroc/make_breakpoints) | `vendor/make_breakpoints` | ld65 symbols → Mesen breakpoints |

`retrotool` can drive the whole chain from a `project.toml` or MBXML file.
Users can pick either **ca65** (shipped here) or **asar** (existing retrotool
asm module) as the assembler, per project.

## Installation

```sh
pip install retrotool[libsfx]      # 0.9.0+
```

Base `retrotool` stays pure-python; the toolchain is opt-in via this extra.

## Setup (development)

```sh
git submodule update --init --recursive
cd packages/retrotool-libsfx
python -m pip install build
python -m build --wheel
```

On platforms where not every submodule builds cleanly (yet), `setup.py` skips
missing submodules with a warning rather than failing — useful during the
0.8.x → 0.9.0 staged rollout.

## Usage

```python
from retrotool_libsfx import (
    superfamiconv_binary, superfamicheck_binary,
    ca65_binary, ld65_binary, brr_encoder_binary, lz4_binary,
    libsfx_include_dir, libsfx_config_dir,
    run_superfamiconv, run_ca65, run_ld65,
    ToolNotBundledError,
)

run_superfamiconv(["tiles", "-i", "bg.png", "-d", "bg.tiles", "-B", "4"])
```

## License

This wrapper package: MIT. Vendored tools retain their own licenses; see each
submodule's LICENSE file.

# retrotool-superfamiconv

Bundled [SuperFamiconv](https://github.com/Optiroc/SuperFamiconv) binary for use
by the `retrotool` graphics pipeline.

This package builds the SuperFamiconv C++ tool from vendored source during
wheel construction and ships the resulting binary inside the wheel. Users get
a working executable via `pip install retrotool[graphics]` without needing a
C++ toolchain.

## Setup (development)

```sh
git submodule add https://github.com/Optiroc/SuperFamiconv \
    packages/retrotool-superfamiconv/vendor/SuperFamiconv
git submodule update --init --recursive
```

## Build

```sh
cd packages/retrotool-superfamiconv
pip install build
python -m build --wheel
```

Wheels for all supported platforms are produced in CI via `cibuildwheel`.

## Usage

```python
from retrotool_superfamiconv import binary_path, run

run(["tiles", "-i", "input.png", "-d", "tiles.bin", "-B", "4"])
```

## License

This wrapper package: MIT. SuperFamiconv itself is MIT by David Lindecrantz;
see `vendor/SuperFamiconv/LICENSE`.

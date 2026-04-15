# retrotool-asar

Bundled [asar](https://github.com/RPGHacker/asar) (RPGHacker) standalone
65816 patcher CLI for retrotool. Installing this wheel makes `asar`
available to `retrotool.asm.patcher` without a system asar on PATH.

## Usage

```python
from retrotool_asar import asar_binary, run_asar

run_asar(["patch.asm", "rom.sfc"])
```

`retrotool.asm.patcher.apply_patch` prefers the bundled binary when this
package is installed, else falls back to `asar` on PATH.

## License

asar is MIT (RPGHacker, Alcaro, et al.). This wheel redistributes the
compiled binary under the same license. See `vendor/asar/LICENSE.txt`.

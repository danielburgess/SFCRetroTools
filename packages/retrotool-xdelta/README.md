# retrotool-xdelta

Bundled [xdelta3](https://github.com/jmacd/xdelta) binary for retrotool's
mbuild diff pipeline. Installing this wheel makes `xdelta` diff output
available to `retrotool.mbuild` without requiring a system `xdelta3` on
PATH.

## Usage

```python
from retrotool_xdelta import xdelta3_binary, run_xdelta3

run_xdelta3(["-e", "-f", "-s", "orig.sfc", "mod.sfc", "out.xdelta"])
```

`retrotool.mbuild.diff.write_xdelta` prefers the bundled binary when this
package is installed, else falls back to `xdelta3` on PATH, else returns
a skipped `DiffResult` with an install hint (when `required=False`).

## License

xdelta3 is Apache-2.0 (Joshua MacDonald). This wheel redistributes the
compiled binary under the same license. See `vendor/xdelta/xdelta3/COPYING`.

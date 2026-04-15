# MBXML examples

`demo.mbxml` — minimal retrotool MBXML exercising the extensions over
MBuild 1.29: unified `<bin codec=>`, `<graphics>`, `<script>`, `<asar>`,
variable interpolation, `if=` conditionals, and `<include>`. The supporting
build files under `out/` are placeholders — copy this directory and swap in
your own assets.

Build:

```
VERSION=v1 LOCALE=en retrotool build demo.mbxml
```

Extract (round-trips files back out of `base.sfc`):

```
retrotool extract demo.mbxml
```

Migrate a legacy MBuild 1.29 file to the unified form:

```
retrotool migrate legacy.mbxml --in-place
```

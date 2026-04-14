"""Named compression scheme registry. Projects reference schemes by name."""
from __future__ import annotations

from typing import Callable

from retrotool.compression.base import Codec
from retrotool.compression.lzss import (
    LZSSCodec,
    LZSSParams,
    PARAMS_LEGACY,
    PARAMS_RBSHURA,
    PARAMS_ZAMN,
)
from retrotool.compression.rle import RLECodec, RLEParams

_REGISTRY: dict[str, Callable[[dict], Codec]] = {}


def register(name: str, factory: Callable[[dict], Codec]) -> None:
    _REGISTRY[name] = factory


def get(name: str, params: dict | None = None) -> Codec:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown compression scheme: {name!r}. Known: {sorted(_REGISTRY)}")
    return _REGISTRY[name](params or {})


def list_schemes() -> list[str]:
    return sorted(_REGISTRY)


def _lzss_factory(params: dict) -> Codec:
    preset = params.get("preset")
    presets = {"rbshura": PARAMS_RBSHURA, "zamn": PARAMS_ZAMN, "legacy": PARAMS_LEGACY}
    if preset:
        return LZSSCodec(presets[preset])
    p = LZSSParams(**{k: v for k, v in params.items() if k != "preset"})
    return LZSSCodec(p)


def _rle_factory(params: dict) -> Codec:
    p = RLEParams(**params) if params else RLEParams()
    return RLECodec(p)


register("lzss", _lzss_factory)
register("lzss-rbshura", lambda _: LZSSCodec(PARAMS_RBSHURA))
register("lzss-zamn", lambda _: LZSSCodec(PARAMS_ZAMN))
register("lzss-legacy", lambda _: LZSSCodec(PARAMS_LEGACY))
register("rle", _rle_factory)

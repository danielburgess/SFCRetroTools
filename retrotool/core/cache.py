"""SHA-256 build cache. Maps input key → stored artifact path."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union

BytesLike = Union[bytes, bytearray, memoryview]


def sha256_bytes(data: BytesLike) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sha256_file(path: Union[str, Path], chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(chunk), b''):
            h.update(block)
    return h.hexdigest()


def sha256_many(items: Iterable[BytesLike]) -> str:
    h = hashlib.sha256()
    for item in items:
        h.update(bytes(item))
    return h.hexdigest()


@dataclass
class CacheEntry:
    key: str
    artifact: Path
    meta: dict


class BuildCache:
    """Filesystem cache keyed by SHA-256. Stores artifact + JSON metadata."""

    def __init__(self, root: Union[str, Path]):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.root / f"{key}.bin", self.root / f"{key}.json"

    def has(self, key: str) -> bool:
        artifact, meta = self._paths(key)
        return artifact.exists() and meta.exists()

    def get(self, key: str) -> Optional[CacheEntry]:
        artifact, meta = self._paths(key)
        if not (artifact.exists() and meta.exists()):
            return None
        return CacheEntry(key=key, artifact=artifact, meta=json.loads(meta.read_text()))

    def put(self, key: str, data: BytesLike, meta: Optional[dict] = None) -> CacheEntry:
        artifact, meta_path = self._paths(key)
        artifact.write_bytes(bytes(data))
        meta_path.write_text(json.dumps(meta or {}, indent=2))
        return CacheEntry(key=key, artifact=artifact, meta=meta or {})

    def invalidate(self, key: str) -> None:
        for p in self._paths(key):
            p.unlink(missing_ok=True)

    def clear(self) -> None:
        for p in self.root.iterdir():
            if p.suffix in ('.bin', '.json'):
                p.unlink()

"""Persistent geometry cache.

LEGO sets reuse the same elements constantly, both within one set and
across every set ever made, so converted geometry is stored once and
reused forever:

    data/cache/<provider>/<aa>/<model_id>.stl

The two-character shard keeps directories small on filesystems that slow
down with tens of thousands of entries.  Metadata (source version,
converter version, hash, dimensions) lives in SQLite so a cache entry can
be invalidated when either the upstream model or our conversion changes.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ..db import Database
from ..ldraw.stl import validate_stl_file
from ..providers.base import ModelProvider, ModelResult

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(slots=True)
class CachedGeometry:
    cache_key: str
    provider: str
    model_id: str
    path: Path
    size_bytes: int
    triangles: int
    dimensions: tuple[float, float, float]
    sha256: str
    from_cache: bool

    def to_dict(self) -> dict:
        return {
            "cache_key": self.cache_key, "provider": self.provider,
            "model_id": self.model_id, "size_bytes": self.size_bytes,
            "triangles": self.triangles, "dimensions_mm": list(self.dimensions),
            "sha256": self.sha256, "from_cache": self.from_cache,
        }


class CacheService:
    def __init__(self, db: Database, cache_directory: Path):
        self.db = db
        self.root = Path(cache_directory)
        self.root.mkdir(parents=True, exist_ok=True)

    # --- paths ------------------------------------------------------------
    def path_for(self, provider: str, model_id: str) -> Path:
        """Deterministic, traversal-proof cache path for a model."""
        safe_provider = _SAFE_ID.sub("_", provider)[:32] or "unknown"
        safe_id = _SAFE_ID.sub("_", model_id)[:96] or "model"
        shard = hashlib.sha1(safe_id.encode()).hexdigest()[:2]
        path = (self.root / safe_provider / shard / f"{safe_id}.stl").resolve()
        # Defence in depth: never escape the cache root.
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError(f"unsafe cache path for {provider}:{model_id}")
        return path

    # --- lookup / store ---------------------------------------------------
    def lookup(self, provider: ModelProvider, model_id: str) -> CachedGeometry | None:
        """Return a valid cache entry, or ``None`` if it must be rebuilt."""
        cache_key = f"{provider.name}:{model_id}"
        row = self.db.query_one(
            "SELECT * FROM geometry_cache WHERE cache_key = ?", (cache_key,))
        if row is None:
            return None
        if (row["converter_version"] != provider.converter_version
                or row["source_version"] != provider.source_version):
            self.invalidate(cache_key)          # upstream or converter changed
            return None
        path = Path(row["stl_path"])
        if not path.exists() or path.stat().st_size != row["size_bytes"]:
            self.invalidate(cache_key)
            return None

        self.db.conn.execute(
            "UPDATE geometry_cache SET last_used_at = ? WHERE cache_key = ?",
            (time.time(), cache_key))
        self.db.conn.commit()
        return CachedGeometry(
            cache_key=cache_key, provider=row["provider"], model_id=row["model_id"],
            path=path, size_bytes=row["size_bytes"], triangles=row["triangles"],
            dimensions=(row["dim_x"], row["dim_y"], row["dim_z"]),
            sha256=row["sha256"], from_cache=True)

    def store(self, provider: ModelProvider, result: ModelResult) -> CachedGeometry:
        cache_key = f"{provider.name}:{result.model_id}"
        digest = sha256_file(result.path)
        now = time.time()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO geometry_cache "
                "(cache_key, provider, model_id, source_version, converter_version, "
                " stl_path, sha256, size_bytes, triangles, dim_x, dim_y, dim_z, "
                " created_at, last_used_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cache_key, provider.name, result.model_id, result.source_version,
                 provider.converter_version, str(result.path), digest,
                 result.size_bytes, result.triangles,
                 result.dimensions[0], result.dimensions[1], result.dimensions[2],
                 now, now))
        return CachedGeometry(
            cache_key=cache_key, provider=provider.name, model_id=result.model_id,
            path=result.path, size_bytes=result.size_bytes, triangles=result.triangles,
            dimensions=result.dimensions, sha256=digest, from_cache=False)

    def get_or_build(self, provider: ModelProvider, model_id: str) -> CachedGeometry:
        """Return cached geometry, converting it only if we do not have it.

        This is the single place that guarantees a shape is converted at most
        once, no matter how many inventory entries or jobs request it.
        """
        hit = self.lookup(provider, model_id)
        if hit is not None:
            return hit

        destination = self.path_for(provider.name, model_id)
        result = provider.build_stl(model_id, destination)

        validation = validate_stl_file(destination)
        if not validation.ok:
            destination.unlink(missing_ok=True)
            raise ValueError(
                f"STL validation failed for {model_id}: {'; '.join(validation.errors)}")
        return self.store(provider, result)

    # --- maintenance ------------------------------------------------------
    def invalidate(self, cache_key: str, *, delete_file: bool = True) -> None:
        row = self.db.query_one(
            "SELECT stl_path FROM geometry_cache WHERE cache_key = ?", (cache_key,))
        if row and delete_file:
            path = Path(row["stl_path"])
            try:
                if path.is_file() and str(path.resolve()).startswith(str(self.root.resolve())):
                    path.unlink()
            except OSError:
                pass
        with self.db.connect() as conn:
            conn.execute("DELETE FROM geometry_cache WHERE cache_key = ?", (cache_key,))

    def purge_older_than(self, seconds: float) -> int:
        """Drop cache entries unused for longer than ``seconds``."""
        cutoff = time.time() - seconds
        rows = self.db.query(
            "SELECT cache_key FROM geometry_cache WHERE last_used_at < ?", (cutoff,))
        for row in rows:
            self.invalidate(row["cache_key"])
        return len(rows)

    def stats(self) -> dict:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b FROM geometry_cache")
        return {"models": int(row["n"]), "bytes": int(row["b"]),
                "directory": str(self.root)}


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()

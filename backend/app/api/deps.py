"""Shared application context: one place that wires everything together."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from ..config import Settings, get_settings
from ..db import Database
from ..providers.registry import ProviderRegistry
from ..services.cache_service import CacheService
from ..services.job_service import JobManager
from ..services.zip_service import ZipService
from ..workers.generation_worker import GenerationWorker

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    settings: Settings
    db: Database
    registry: ProviderRegistry
    cache: CacheService
    zips: ZipService
    jobs: JobManager
    worker: GenerationWorker

    @classmethod
    def create(cls, settings: Settings | None = None) -> "AppContext":
        settings = settings or get_settings()
        settings.ensure_directories()
        db = Database(settings.sqlite_path)
        registry = ProviderRegistry(settings, db)
        cache = CacheService(db, settings.cache_directory)
        zips = ZipService(settings.output_directory, settings.max_zip_bytes)
        jobs = JobManager(db)
        worker = GenerationWorker(settings, registry, cache, zips, jobs)
        return cls(settings, db, registry, cache, zips, jobs, worker)

    def cleanup(self) -> dict:
        """Remove expired jobs and stale cache entries."""
        expired = self.jobs.purge_expired(self.settings.job_retention)
        for job_id in expired:
            self.zips.cleanup_job(job_id)
        directories = self.zips.purge_older_than(self.settings.job_retention)
        models = self.cache.purge_older_than(self.settings.cache_retention)
        if expired or directories or models:
            log.info("cleanup: %d jobs, %d directories, %d cached models",
                     len(expired), directories, models)
        return {"jobs": len(expired), "directories": directories, "models": models}


def get_context(request: Request) -> AppContext:
    context = getattr(request.app.state, "context", None)
    if context is None:                                    # pragma: no cover
        raise HTTPException(503, "Application is still starting up.")
    return context


class RateLimiter:
    """Fixed-window per-IP limiter for the expensive endpoints."""

    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str) -> None:
        now = time.time()
        cutoff = now - self.window
        hits = [t for t in self._hits.get(key, []) if t > cutoff]
        if len(hits) >= self.limit:
            retry_after = int(self.window - (now - hits[0])) + 1
            raise HTTPException(
                429, f"Too many requests. Try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)})
        hits.append(now)
        self._hits[key] = hits
        if len(self._hits) > 4096:                         # bound memory
            self._hits = {k: v for k, v in self._hits.items()
                          if v and v[-1] > cutoff}


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"

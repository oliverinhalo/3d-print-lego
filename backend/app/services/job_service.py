"""Job registry and progress event bus.

Jobs live in memory while they run and are persisted to SQLite so a finished
ZIP survives a restart.  Progress is broadcast to any number of subscribers
(the browser's SSE stream, tests, a CLI) through per-subscriber queues; a
slow consumer drops events rather than stalling the worker.

Every subscriber is also handed a replay of the events emitted so far, so a
browser that connects a moment after the job starts still sees the set
preview and the parts discovered before it arrived.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..db import Database
from ..models.job import Job, JobStatus

log = logging.getLogger(__name__)

#: Events retained per job for replay to late subscribers.
REPLAY_LIMIT = 2000
#: Pending events per subscriber before the slowest are dropped.
SUBSCRIBER_QUEUE = 512


@dataclass(slots=True)
class JobChannel:
    """Fan-out for one job's progress events."""

    history: deque = field(default_factory=lambda: deque(maxlen=REPLAY_LIMIT))
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    closed: bool = False


class JobManager:
    def __init__(self, db: Database, max_jobs: int = 500):
        self.db = db
        self.jobs: dict[str, Job] = {}
        self.channels: dict[str, JobChannel] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.max_jobs = max_jobs

    # --- registry ---------------------------------------------------------
    def register(self, job: Job) -> Job:
        self.jobs[job.id] = job
        self.channels[job.id] = JobChannel()
        self.persist(job)
        self._evict()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def _evict(self) -> None:
        """Forget the oldest finished jobs once the registry grows too large."""
        if len(self.jobs) <= self.max_jobs:
            return
        finished = sorted(
            (j for j in self.jobs.values() if j.status.terminal),
            key=lambda j: j.finished_at or j.created_at)
        for job in finished[:len(self.jobs) - self.max_jobs]:
            self.jobs.pop(job.id, None)
            self.channels.pop(job.id, None)

    # --- persistence ------------------------------------------------------
    def persist(self, job: Job) -> None:
        try:
            with self.db.connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO jobs "
                    "(id, query, set_num, status, stage, payload, zip_path, zip_name, "
                    " zip_bytes, created_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (job.id, job.query, job.set_num, job.status.value, job.stage.value,
                     json.dumps(job.summary()), job.zip_path, job.zip_name,
                     job.zip_bytes, job.created_at, job.finished_at))
        except Exception:                      # persistence must never break a job
            log.exception("failed to persist job %s", job.id)

    def load_persisted(self, job_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            payload = {}
        payload.update({"job_id": row["id"], "status": row["status"],
                        "zip_name": row["zip_name"], "zip_bytes": row["zip_bytes"]})
        return payload

    def zip_path_for(self, job_id: str) -> str | None:
        job = self.jobs.get(job_id)
        if job and job.zip_path:
            return job.zip_path
        row = self.db.query_one("SELECT zip_path FROM jobs WHERE id = ?", (job_id,))
        return row["zip_path"] if row else None

    # --- events -----------------------------------------------------------
    def publish(self, job_id: str, event_type: str, **payload: Any) -> None:
        """Broadcast one progress event. Safe to call from the worker task."""
        channel = self.channels.get(job_id)
        if channel is None or channel.closed:
            return
        event = {"type": event_type, "ts": time.time(), **payload}
        channel.history.append(event)
        for queue in list(channel.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled browser must not slow the pipeline down.
                log.debug("dropping event for slow subscriber on job %s", job_id)

    def close_channel(self, job_id: str) -> None:
        channel = self.channels.get(job_id)
        if channel is None:
            return
        channel.closed = True
        for queue in list(channel.subscribers):
            try:
                queue.put_nowait(None)         # sentinel: end of stream
            except asyncio.QueueFull:
                pass

    async def subscribe(self, job_id: str) -> AsyncIterator[dict]:
        """Yield this job's events, starting with everything already emitted."""
        channel = self.channels.get(job_id)
        if channel is None:
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE)
        replay = list(channel.history)
        channel.subscribers.append(queue)
        try:
            for event in replay:
                yield event
            if channel.closed:
                return
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            if queue in channel.subscribers:
                channel.subscribers.remove(queue)

    # --- lifecycle --------------------------------------------------------
    def cancel(self, job_id: str) -> bool:
        task = self.tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def shutdown(self) -> None:
        for task in list(self.tasks.values()):
            if not task.done():
                task.cancel()
        for task in list(self.tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def purge_expired(self, retention_seconds: float) -> list[str]:
        """Return ids of jobs whose retention window has passed."""
        cutoff = time.time() - retention_seconds
        rows = self.db.query(
            "SELECT id FROM jobs WHERE COALESCE(finished_at, created_at) < ?", (cutoff,))
        expired = [row["id"] for row in rows]
        if expired:
            with self.db.connect() as conn:
                conn.executemany("DELETE FROM jobs WHERE id = ?",
                                 [(job_id,) for job_id in expired])
        for job_id in expired:
            self.jobs.pop(job_id, None)
            self.channels.pop(job_id, None)
        return expired

    def stats(self) -> dict:
        active = sum(1 for j in self.jobs.values() if j.status is JobStatus.RUNNING)
        return {"in_memory": len(self.jobs), "running": active,
                "persisted": self.db.table_count("jobs")}

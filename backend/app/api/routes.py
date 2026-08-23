"""HTTP API.

    POST /api/generate           -> { job_id }
    GET  /api/jobs/{id}          -> job summary (polling fallback)
    GET  /api/jobs/{id}/events   -> Server-Sent Events progress stream
    GET  /api/jobs/{id}/parts    -> full part list with statuses
    POST /api/jobs/{id}/retry    -> retry only the failed parts
    GET  /api/jobs/{id}/download -> the ZIP
    GET  /api/sets/{number}      -> set preview without starting a job
    GET  /api/health             -> provider and cache diagnostics

The route layer only validates input and delegates: the pipeline lives in
``workers/generation_worker.py``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from typing import Literal

from pydantic import BaseModel, Field

from ..models.job import Job, JobStatus
from ..providers.base import ProviderError, ProviderUnavailable, SetNotFound
from ..services.normalize import InvalidSetNumber, normalize_set_number
from ..services.plate_service import BED_PRESETS
from .deps import AppContext, RateLimiter, client_key, get_context

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_limiter: RateLimiter | None = None


def limiter(request: Request) -> RateLimiter:
    global _limiter
    if _limiter is None:
        settings = get_context(request).settings
        _limiter = RateLimiter(settings.rate_limit_requests, settings.rate_limit_window)
    return _limiter


class GenerateRequest(BaseModel):
    set_number: str = Field(..., min_length=1, max_length=64,
                            description='LEGO set number, e.g. "77263" or "#77263"')
    include_spares: bool = Field(False, description="Include spare parts in the pack")
    color_mode: Literal["none", "family", "exact"] | None = Field(
        None, description='How to group parts onto plates: ignore colour, '
                          'group similar colours, or one group per exact colour')
    bed_preset: str | None = Field(
        None, max_length=32, description="Printer whose bed size to arrange for")
    plate_output: Literal["separate", "project", "both"] | None = Field(
        None, description='File layout: one 3MF per plate, a single project '
                          'holding every plate, or both')


class GenerateResponse(BaseModel):
    job_id: str
    set_num: str


@router.post("/generate", response_model=GenerateResponse, status_code=202)
async def generate(payload: GenerateRequest, request: Request,
                   context: AppContext = Depends(get_context)) -> GenerateResponse:
    """Validate the set number, create a job, and start it in the background."""
    limiter(request).check(client_key(request))

    try:
        set_num = normalize_set_number(payload.set_number)
    except InvalidSetNumber as exc:
        raise HTTPException(400, str(exc)) from exc

    health = context.registry.health()
    if not health["ready"]:
        raise HTTPException(
            503, "The catalogue or parts library is not installed. "
                 "Run: python scripts/bootstrap_data.py")

    settings = context.settings
    bed = payload.bed_preset or settings.bed_preset
    if bed not in BED_PRESETS:
        raise HTTPException(400, f"Unknown printer {bed!r}.")

    job = Job(query=payload.set_number.strip(), set_num=set_num,
              include_spares=payload.include_spares,
              color_mode=payload.color_mode or settings.color_mode,
              bed_preset=bed,
              plate_output=payload.plate_output or settings.plate_output)
    context.jobs.register(job)

    task = asyncio.create_task(context.worker.run(job))
    context.jobs.tasks[job.id] = task
    task.add_done_callback(lambda _t: context.jobs.tasks.pop(job.id, None))

    return GenerateResponse(job_id=job.id, set_num=set_num)


@router.get("/sets/{set_number}")
async def preview_set(set_number: str, request: Request,
                      context: AppContext = Depends(get_context)) -> dict:
    """Look up a set without generating anything (used by the homepage)."""
    limiter(request).check(client_key(request))
    try:
        set_num = normalize_set_number(set_number)
    except InvalidSetNumber as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        lego_set = await asyncio.to_thread(context.registry.set_provider.get_set, set_num)
    except SetNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ProviderUnavailable, ProviderError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"set": lego_set.to_dict()}


@router.get("/sets")
async def search_sets(q: str, request: Request, limit: int = 8,
                      context: AppContext = Depends(get_context)) -> dict:
    """Free-text search, used for suggestions when a number is not found."""
    limiter(request).check(client_key(request))
    text = (q or "").strip()
    if len(text) < 2:
        return {"results": []}
    try:
        results = await asyncio.to_thread(
            context.registry.set_provider.search, text[:64], min(max(limit, 1), 25))
    except (ProviderUnavailable, ProviderError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"results": [s.to_dict() for s in results]}


def _require_job(context: AppContext, job_id: str) -> Job:
    job = context.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "That generation job no longer exists.")
    return job


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, context: AppContext = Depends(get_context)) -> dict:
    job = context.jobs.get(job_id)
    if job is not None:
        return job.summary()
    persisted = context.jobs.load_persisted(job_id)      # survives a restart
    if persisted is None:
        raise HTTPException(404, "That generation job no longer exists.")
    return persisted


@router.get("/jobs/{job_id}/parts")
async def job_parts(job_id: str, context: AppContext = Depends(get_context)) -> dict:
    job = _require_job(context, job_id)
    return {
        "job_id": job.id,
        "parts": [p.to_dict() for p in sorted(job.parts.values(),
                                              key=lambda p: (-p.quantity, p.part_num))],
        "failed": [p.to_dict() for p in job.failed_parts],
    }


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request,
                     context: AppContext = Depends(get_context)) -> StreamingResponse:
    """Server-Sent Events stream of generation progress."""
    job = _require_job(context, job_id)

    async def event_stream():
        # Send the current summary first so a reconnecting client is never blank.
        yield _sse({"type": "snapshot", "job": job.summary()})
        try:
            async for event in context.jobs.subscribe(job_id):
                if await request.is_disconnected():
                    break
                yield _sse(event)
        except asyncio.CancelledError:            # client went away
            raise
        finally:
            log.debug("SSE stream closed for job %s", job_id)
        yield _sse({"type": "stream_end", "job": job.summary()})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",          # stop proxies buffering the stream
        },
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


@router.post("/jobs/{job_id}/retry")
async def retry_failed(job_id: str, request: Request,
                       context: AppContext = Depends(get_context)) -> dict:
    """Re-attempt only the parts that failed, keeping successful work."""
    limiter(request).check(client_key(request))
    job = _require_job(context, job_id)
    if job.status is JobStatus.RUNNING:
        raise HTTPException(409, "This job is still running.")
    if not job.failed_parts:
        raise HTTPException(400, "There are no failed parts to retry.")

    context.jobs.channels[job.id].closed = False
    task = asyncio.create_task(context.worker.retry_failed(job))
    context.jobs.tasks[job.id] = task
    task.add_done_callback(lambda _t: context.jobs.tasks.pop(job.id, None))
    return {"job_id": job.id, "retrying": len(job.failed_parts)}


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str, context: AppContext = Depends(get_context)) -> dict:
    job = _require_job(context, job_id)
    cancelled = context.jobs.cancel(job_id)
    return {"job_id": job.id, "cancelled": cancelled}


@router.get("/jobs/{job_id}/download")
async def download(job_id: str, context: AppContext = Depends(get_context)) -> FileResponse:
    """Serve the generated ZIP.

    The path comes from our own job record, never from the request, and is
    re-checked against the output directory before being served.
    """
    zip_path = context.jobs.zip_path_for(job_id)
    if not zip_path:
        raise HTTPException(404, "This job has no download yet.")

    path = Path(zip_path).resolve()
    output_root = context.settings.output_directory.resolve()
    if not str(path).startswith(str(output_root)) or not path.is_file():
        raise HTTPException(404, "The download has expired and been cleaned up.")

    return FileResponse(
        path, media_type="application/zip", filename=path.name,
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'})


@router.get("/browse")
async def browse_sets(request: Request,
                      q: str = "", theme: int | None = None, year: int | None = None,
                      min_parts: int = 1, max_parts: int = 0,
                      sort: str = "popular", limit: int = 24, offset: int = 0,
                      context: AppContext = Depends(get_context)) -> dict:
    """Browse the catalogue: what the set picker is built on.

    Everything comes from the local dataset, so this is instant and needs no
    third-party site.
    """
    limiter(request).check(client_key(request))
    provider = context.registry.csv_provider
    if not provider.available:
        raise HTTPException(503, "The LEGO catalogue has not been imported yet.")

    try:
        result = await asyncio.to_thread(
            provider.browse,
            query=(q or "").strip()[:64], theme_id=theme, year=year,
            min_parts=max(int(min_parts), 1), max_parts=max(int(max_parts), 0),
            sort=sort if sort in ("popular", "newest", "smallest", "name") else "popular",
            limit=min(max(int(limit), 1), 60), offset=max(int(offset), 0))
    except (ProviderUnavailable, ProviderError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return result


@router.get("/themes")
async def list_themes(request: Request,
                      context: AppContext = Depends(get_context)) -> dict:
    limiter(request).check(client_key(request))
    provider = context.registry.csv_provider
    if not provider.available:
        raise HTTPException(503, "The LEGO catalogue has not been imported yet.")
    themes = await asyncio.to_thread(provider.themes, 40)
    return {"themes": themes}


@router.get("/options")
async def options(context: AppContext = Depends(get_context)) -> dict:
    """Printer presets and colour modes, so the UI never hard-codes them."""
    return {
        "printers": [
            {"id": key, "bed": list(size),
             "label": key.replace("_", " ").title()}
            for key, size in BED_PRESETS.items()
        ],
        "plate_outputs": [
            {"id": "both", "label": "Both",
             "detail": "One project file plus a file per plate"},
            {"id": "project", "label": "One project file",
             "detail": "Every plate in a single file, named by colour"},
            {"id": "separate", "label": "A file per plate",
             "detail": "Plain 3MF, opens in any slicer"},
        ],
        "color_modes": [
            {"id": "none", "label": "Any colour",
             "detail": "Pack by size only - fewest plates"},
            {"id": "family", "label": "Similar colours",
             "detail": "All reds together, all blues together"},
            {"id": "exact", "label": "Exact colours",
             "detail": "One group per LEGO colour code"},
        ],
        "defaults": {
            "bed_preset": context.settings.bed_preset,
            "color_mode": context.settings.color_mode,
            "plate_output": context.settings.plate_output,
        },
    }


@router.get("/health")
async def health(context: AppContext = Depends(get_context)) -> dict:
    return {
        "status": "ok",
        "providers": context.registry.health(),
        "cache": context.cache.stats(),
        "jobs": context.jobs.stats(),
        "settings": {
            "max_concurrent_downloads": context.settings.max_concurrent_downloads,
            "max_pieces_per_job": context.settings.max_pieces_per_job,
            "part_scale": context.settings.part_scale,
            "auto_orient": context.settings.auto_orient,
        },
    }

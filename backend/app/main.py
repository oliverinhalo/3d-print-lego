"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.deps import AppContext
from .api.routes import router
from .config import ROOT_DIR, get_settings

log = logging.getLogger(__name__)

CLEANUP_INTERVAL = 600           # seconds between housekeeping passes
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


async def _cleanup_loop(app: FastAPI) -> None:
    """Periodically drop expired jobs, ZIPs and stale cache entries."""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)
            await asyncio.to_thread(app.state.context.cleanup)
        except asyncio.CancelledError:
            raise
        except Exception:                                  # pragma: no cover
            log.exception("cleanup pass failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    app.state.context = AppContext.create(settings)
    health = app.state.context.registry.health()
    if not health["ready"]:
        log.warning("Data sources are not ready. Run: python scripts/bootstrap_data.py")
    else:
        log.info("Providers ready: sets=%s models=%s",
                 health["set_provider"], ",".join(
                     p["name"] for p in health["sources"].values() if p.get("available")))

    cleanup = asyncio.create_task(_cleanup_loop(app))
    try:
        yield
    finally:
        cleanup.cancel()
        await app.state.context.jobs.shutdown()
        app.state.context.db.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="LEGO Set to Printable STL Generator",
        description="Turn a LEGO set number into a ZIP of printable STL files.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    app.include_router(router)

    # In production the built frontend is served by this same process, so a
    # home server needs one port and no reverse proxy.
    if FRONTEND_DIST.is_dir():
        assets = FRONTEND_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            candidate = (FRONTEND_DIST / full_path).resolve()
            if (full_path and candidate.is_file()
                    and str(candidate).startswith(str(FRONTEND_DIST.resolve()))):
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return JSONResponse({
                "name": "LEGO Set to Printable STL Generator",
                "docs": "/docs",
                "note": "Frontend is not built. Run: npm --prefix frontend run build",
            })

    return app


app = create_app()

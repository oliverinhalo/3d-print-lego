#!/usr/bin/env python3
"""Run the application.

    python run.py              # serve API + built frontend on one port
    python run.py --reload     # backend with auto-reload, for development
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn  # noqa: E402

from app.config import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--reload", action="store_true", help="auto-reload on changes")
    parser.add_argument("--workers", type=int, default=1,
                        help="worker processes (jobs are per-process; keep at 1 "
                             "unless you put a shared job store in front)")
    args = parser.parse_args()

    settings.ensure_directories()
    if not (ROOT / "frontend" / "dist").is_dir():
        print("Note: frontend/dist not found — serving the API only.\n"
              "      Build the UI with:  npm --prefix frontend install && "
              "npm --prefix frontend run build\n")

    print(f"→ http://{args.host}:{args.port}\n")
    uvicorn.run("app.main:app", host=args.host, port=args.port,
                reload=args.reload, workers=args.workers if not args.reload else 1,
                log_level=settings.log_level.lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the application.

    python run.py              # serve API + built frontend on one port
    python run.py --reload     # backend with auto-reload, for development
    python run.py --no-bootstrap   # never auto-download data

On first start the catalogue and the LDraw parts library are downloaded
automatically if they are missing (about 150 MB, a few minutes). That makes
`docker compose up` a complete install with no follow-up commands. Set
AUTO_BOOTSTRAP=false to disable it and run scripts/bootstrap_data.py yourself.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn  # noqa: E402

from app.config import get_settings  # noqa: E402


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def data_is_missing(settings) -> bool:
    """True when either data source still needs downloading."""
    catalogue = (settings.rebrickable_dir / "sets.csv").is_file()
    library = (settings.ldraw_dir / "parts").is_dir()
    return not (catalogue and library)


def bootstrap(settings) -> bool:
    """Download and import the data sources. Returns True on success.

    ``scripts/bootstrap_data.py`` is a standalone CLI, so it is loaded by
    path rather than imported as a package module.
    """
    script = ROOT / "scripts" / "bootstrap_data.py"
    if not script.is_file():
        print(f"ERROR: {script} is missing; cannot download the data.", flush=True)
        return False

    print("=" * 62, flush=True)
    print("  First run: downloading the LEGO catalogue and parts library.", flush=True)
    print("  This is about 150 MB and happens only once.", flush=True)
    print("=" * 62 + "\n", flush=True)

    spec = importlib.util.spec_from_file_location("bootstrap_data", script)
    if spec is None or spec.loader is None:                     # pragma: no cover
        print("ERROR: could not load the bootstrap script.", flush=True)
        return False
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    try:
        module.fetch_rebrickable(settings, skip_existing=True)
        module.fetch_ldraw(settings, skip_existing=True)
        module.import_catalogue(settings)
    except Exception as exc:                                    # noqa: BLE001
        print(f"\nERROR: data download failed: {exc}", flush=True)
        print("The app will still start, but cannot generate sets until the\n"
              "data is present. Fix the problem and restart, or run:\n"
              "  python scripts/bootstrap_data.py\n", flush=True)
        return False

    print("\nData ready.\n", flush=True)
    return True


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
    parser.add_argument("--no-bootstrap", action="store_true",
                        help="do not download data automatically on first run")
    args = parser.parse_args()

    settings.ensure_directories()

    auto = _env_flag("AUTO_BOOTSTRAP", True) and not args.no_bootstrap
    if data_is_missing(settings):
        if auto:
            bootstrap(settings)
        else:
            print("Note: catalogue/parts library not found. Run:\n"
                  "      python scripts/bootstrap_data.py\n", flush=True)

    if not (ROOT / "frontend" / "dist").is_dir():
        print("Note: frontend/dist not found — serving the API only.\n"
              "      Build the UI with:  npm --prefix frontend install && "
              "npm --prefix frontend run build\n", flush=True)

    print(f"→ http://{args.host}:{args.port}\n", flush=True)
    uvicorn.run("app.main:app", host=args.host, port=args.port,
                reload=args.reload, workers=args.workers if not args.reload else 1,
                log_level=settings.log_level.lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

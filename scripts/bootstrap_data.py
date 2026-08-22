#!/usr/bin/env python3
"""One-time setup: download the catalogue and the parts library.

Two public, freely redistributable sources are fetched:

1. **Rebrickable CSV datasets** (https://rebrickable.com/downloads/) — the
   set catalogue and parts inventories.  No API key, no account.
2. **LDraw Parts Library** (https://library.ldraw.org/updates) — ~24,000
   part definitions, licensed CC BY.  A single ~145 MB archive.

Both are downloaded to ``SOURCE_DIRECTORY`` and the CSVs are imported into
SQLite.  Re-running refreshes the data; ``--skip-existing`` keeps whatever
is already present.

    python scripts/bootstrap_data.py
    python scripts/bootstrap_data.py --skip-existing
    python scripts/bootstrap_data.py --only rebrickable
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.providers.rebrickable_csv import CSV_FILES, import_csv_directory  # noqa: E402

REBRICKABLE_BASE = "https://cdn.rebrickable.com/media/downloads"
LDRAW_ARCHIVE = "https://library.ldraw.org/library/updates/complete.zip"
CHUNK = 1 << 20


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def download(client: httpx.Client, url: str, destination: Path, label: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    started = time.time()
    # Only animate a progress line on a terminal; piped into a log or CI this
    # would otherwise emit one line per megabyte.
    interactive = sys.stdout.isatty()
    with client.stream("GET", url, follow_redirects=True, timeout=120.0) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        done = 0
        with open(temporary, "wb") as fh:
            for chunk in response.iter_bytes(CHUNK):
                fh.write(chunk)
                done += len(chunk)
                if not interactive:
                    continue
                if total:
                    pct = 100 * done / total
                    print(f"\r  {label}: {pct:5.1f}%  {human(done)} / {human(total)}",
                          end="", flush=True)
                else:
                    print(f"\r  {label}: {human(done)}", end="", flush=True)
    temporary.replace(destination)
    prefix = "\r" if interactive else ""
    print(f"{prefix}  {label}: done  {human(destination.stat().st_size)} "
          f"in {time.time() - started:.1f}s" + (" " * 20 if interactive else ""))
    return destination


def fetch_rebrickable(settings, skip_existing: bool) -> None:
    target = settings.rebrickable_dir
    target.mkdir(parents=True, exist_ok=True)
    print("\n== Rebrickable catalogue ==")
    with httpx.Client(headers={"User-Agent": "lego-stl-generator/1.0"}) as client:
        for stem in CSV_FILES:
            csv_path = target / f"{stem}.csv"
            if skip_existing and csv_path.exists():
                print(f"  {stem}.csv: already present, skipping")
                continue
            gz_path = target / f"{stem}.csv.gz"
            try:
                download(client, f"{REBRICKABLE_BASE}/{stem}.csv.gz", gz_path, f"{stem}.csv.gz")
            except httpx.HTTPError as exc:
                print(f"  {stem}: download failed ({exc})")
                continue
            with gzip.open(gz_path, "rb") as src, open(csv_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            gz_path.unlink(missing_ok=True)
    (settings.source_directory / "rebrickable_version.json").write_text(
        json.dumps({"downloaded_at": time.time(),
                    "source": "https://rebrickable.com/downloads/"}, indent=2))


def fetch_ldraw(settings, skip_existing: bool) -> None:
    ldraw_dir = settings.ldraw_dir
    print("\n== LDraw parts library ==")
    if skip_existing and (ldraw_dir / "parts").is_dir():
        print("  already present, skipping")
        return
    archive = settings.source_directory / "ldraw_complete.zip"
    with httpx.Client(headers={"User-Agent": "lego-stl-generator/1.0"}) as client:
        response = client.head(LDRAW_ARCHIVE, follow_redirects=True, timeout=60.0)
        version = response.headers.get("last-modified", "unknown")
        download(client, LDRAW_ARCHIVE, archive, "complete.zip")

    print("  extracting...")
    staging = settings.source_directory / "_ldraw_staging"
    if staging.exists():
        shutil.rmtree(staging)
    with zipfile.ZipFile(archive) as zf:
        # Guard against archive members escaping the target directory.
        for member in zf.namelist():
            resolved = (staging / member).resolve()
            if not str(resolved).startswith(str(staging.resolve())):
                raise RuntimeError(f"unsafe path in archive: {member}")
        zf.extractall(staging)

    extracted = staging / "ldraw" if (staging / "ldraw" / "parts").is_dir() else staging
    if ldraw_dir.exists():
        shutil.rmtree(ldraw_dir)
    shutil.move(str(extracted), str(ldraw_dir))
    shutil.rmtree(staging, ignore_errors=True)
    archive.unlink(missing_ok=True)

    (settings.source_directory / "ldraw_version.json").write_text(
        json.dumps({"version": version, "downloaded_at": time.time(),
                    "source": LDRAW_ARCHIVE,
                    "license": "CC BY 2.0 / CC BY 4.0 — see ldraw/CAreadme.txt"}, indent=2))
    count = sum(1 for f in (ldraw_dir / "parts").iterdir() if f.suffix.lower() == ".dat")
    print(f"  installed {count} parts  (library version: {version})")


def import_catalogue(settings) -> None:
    print("\n== Importing catalogue into SQLite ==")
    db = Database(settings.sqlite_path)
    started = time.time()
    counts = import_csv_directory(
        db, settings.rebrickable_dir,
        progress=lambda table, n: print(f"  {table}: {n:,} rows"))
    db.set_meta("rebrickable_imported_at", str(time.time()))
    version_file = settings.source_directory / "rebrickable_version.json"
    if version_file.exists():
        db.set_meta("rebrickable_dataset_date",
                    str(json.loads(version_file.read_text()).get("downloaded_at", "")))
    print(f"  imported {sum(counts.values()):,} rows in {time.time() - started:.1f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-existing", action="store_true",
                        help="keep data that is already downloaded")
    parser.add_argument("--only", choices=("rebrickable", "ldraw", "import"),
                        help="run only one step")
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_directories()
    print(f"Source directory: {settings.source_directory}")

    if args.only in (None, "rebrickable"):
        fetch_rebrickable(settings, args.skip_existing)
    if args.only in (None, "ldraw"):
        fetch_ldraw(settings, args.skip_existing)
    if args.only in (None, "rebrickable", "import"):
        import_catalogue(settings)

    print("\nReady. Start the app with:  python run.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

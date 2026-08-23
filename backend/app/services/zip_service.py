"""Builds the downloadable print pack.

Layout (kept deliberately uncluttered — the user opens ``STLs/``, selects
everything and drags it into a slicer):

    LEGO_77263_Print_Pack/
        README.txt
        parts.json
        STLs/
            3001_Brick_2x4_01.stl
            3001_Brick_2x4_02.stl
            ...

Duplicates are written as separate files because that is the one approach
every slicer handles identically — Bambu Studio, OrcaSlicer, Cura and
PrusaSlicer all import N files as N arrangeable objects.  A single cached
geometry is streamed into each copy, so 40 Technic pins cost one conversion.
"""
from __future__ import annotations

import json
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..models.job import Job
from ..models.part import PartStatus, PrintPart
from .naming_service import instance_filename, zip_basename

#: Files bigger than this are streamed rather than read into memory.
COPY_BUFFER = 1 << 20


@dataclass(slots=True)
class ZipResult:
    path: Path
    name: str
    size_bytes: int
    file_count: int
    instance_count: int


def build_readme(job: Job, instance_count: int, unique_count: int,
                 failed: list[PrintPart], *, plate_count: int = 0,
                 include_stls: bool = False, project_name: str | None = None,
                 separate_plates: int = 0) -> str:
    lego_set = job.lego_set
    set_line = (f"{lego_set.name}  (#{lego_set.display_number})"
                if lego_set else job.query)
    total_pieces = sum(p.quantity for p in job.parts.values()
                       if p.status in (PartStatus.READY, PartStatus.CACHED))

    lines = [
        "=" * 68,
        f"  {set_line}",
        "  Printable part pack",
        "=" * 68,
        "",
        f"  {total_pieces} pieces      ({unique_count} unique shapes)",
        f"  Generated:    {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if plate_count:
        lines.append(f"  Build plates: {plate_count}")
    lines.append("")

    if plate_count:
        lines += ["HOW TO PRINT", "-" * 68, ""]

        if project_name:
            lines += [
                "  EASIEST: open this one file",
                "",
                f"      {project_name}",
                "",
                f"  It contains all {plate_count} plates in a single project. Every",
                "  plate is already arranged, and each is named after its colour,",
                "  so you can switch plates inside the slicer and print them in",
                "  turn. Nothing to import, nothing to arrange.",
                "",
                "  This uses Bambu Studio's project format. It should also open in",
                "  OrcaSlicer. If your slicer does not understand it, use the",
                "  Plates folder below instead - same parts, same arrangement.",
                "",
            ]

        if separate_plates:
            heading = "  ALTERNATIVE: one file per plate" if project_name else "  Open one plate at a time"
            lines += [
                heading,
                "",
                "  1. Open the Plates folder.",
                "  2. Open ONE plate file, for example Plate_01.3mf.",
                "  3. It opens already arranged. Slice and print.",
                "  4. Repeat for each plate.",
                "",
                "  Do NOT select every plate at once - each file is one plateful.",
                "  These are plain 3MF files and open in Bambu Studio, OrcaSlicer,",
                "  PrusaSlicer and Cura alike.",
                "",
            ]

        lines += [
            "  You never need to press Auto Arrange: the parts are already",
            "  positioned, spaced and inside the printable area.",
            "",
        ]
        if job.color_mode != "none":
            grouping = ("exact LEGO colour" if job.color_mode == "exact"
                        else "colour family")
            lines += [
                f"  Plates are grouped by {grouping}, and each plate is named",
                "  after its colour, so one filament prints a whole plate.",
                "",
            ]
    if include_stls:
        lines += [
            "INDIVIDUAL STL FILES",
            "-" * 68,
            "  The STLs folder holds one STL per physical piece, if you would",
            "  rather arrange them yourself. With a large set this is hundreds",
            "  of files and most slicers struggle to import them all at once.",
            "",
        ]

    lines += [
        "ABOUT THE MODELS",
        "-" * 68,
        "  Units:      millimetres (Z is up, parts laid flat).",
        "  Geometry:   LDraw Parts Library, licensed CC BY.",
        "              https://library.ldraw.org/",
        "  Inventory:  Rebrickable catalogue data.",
        "              https://rebrickable.com/downloads/",
        "",
        "  LDraw geometry is nominal: a 2x4 brick measures exactly",
        "  32.00 x 16.00 mm, whereas a moulded LEGO brick is 31.80 x 15.80 mm.",
        "  Printed parts may therefore be a touch tight against real bricks.",
        "  Printers also vary; expect to tune tolerances before parts clutch",
        "  well. See the project README for the PART_SCALE setting.",
        "",
        "  These files are for personal use. LEGO is a trademark of the LEGO",
        "  Group, which does not sponsor or endorse this project.",
        "",
    ]

    if job.oversized:
        names = sorted({i.part_num for i in job.oversized})
        lines += [
            "PARTS TOO BIG FOR THIS PRINTER",
            "-" * 68,
            f"  {len(names)} part(s) do not fit the selected bed and were left out:",
            "",
            *(f"  - {name}" for name in names),
            "",
            "  Generate again with a larger printer selected, or print these",
            "  separately after splitting them.",
            "",
        ]

    if failed:
        lines += [
            "PARTS THAT COULD NOT BE GENERATED",
            "-" * 68,
            f"  {len(failed)} part(s) are missing from this pack:",
            "",
        ]
        for part in failed:
            lines.append(f"  - {part.part_num}  x{part.quantity}  {part.name}")
            if part.error:
                lines.append(f"      reason: {part.error}")
        lines += ["", "  See parts.json for full details.", ""]
    return "\n".join(lines)


def build_parts_manifest(job: Job, instances: dict[str, int]) -> dict:
    lego_set = job.lego_set
    return {
        "set": lego_set.to_dict() if lego_set else None,
        "generated_at": time.time(),
        "generator": "lego-stl-generator/1.0",
        "units": "millimetres",
        "totals": {
            "unique_parts": job.unique_parts,
            "distinct_geometries": job.distinct_geometries,
            "total_pieces": job.total_pieces,
            "stl_files": sum(instances.values()),
            "failed_parts": len(job.failed_parts),
            "plates": len(job.plates),
        },
        "options": {
            "color_mode": job.color_mode,
            "bed_preset": job.bed_preset,
        },
        "plates": [p.to_dict() for p in job.plates],
        "parts": [
            {
                **part.to_dict(),
                "files": instances.get(part.part_num, 0),
                "colors": part.color_names,
            }
            for part in sorted(job.parts.values(), key=lambda p: p.part_num)
        ],
        "sources": {
            "inventory": "Rebrickable (https://rebrickable.com/downloads/)",
            "geometry": "LDraw Parts Library (https://library.ldraw.org/), CC BY",
        },
    }


class ZipService:
    def __init__(self, output_directory: Path, max_bytes: int = 2 << 30):
        self.root = Path(output_directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    def job_directory(self, job_id: str) -> Path:
        safe = "".join(ch for ch in job_id if ch.isalnum())[:32]
        if not safe:
            raise ValueError("invalid job id")
        path = (self.root / safe).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError("invalid job id")
        return path

    def build(self, job: Job, geometry_paths: dict[str, Path],
              progress: Callable[[int, int], None] | None = None,
              *, plate_files: dict[int, Path] | None = None,
              project_file: Path | None = None,
              include_stls: bool = False) -> ZipResult:
        """Write the print pack for ``job``.

        ``geometry_paths`` maps ``part_num`` -> cached STL path.
        ``plate_files`` maps plate index -> a pre-arranged 3MF.

        The plates are the intended way to print: each one opens ready to
        slice. Individual STLs are optional because a large set is hundreds of
        files, which is exactly the import problem the plates remove.
        """
        lego_set = job.lego_set
        set_number = lego_set.display_number if lego_set else "set"
        basename = zip_basename(set_number)
        directory = self.job_directory(job.id)
        directory.mkdir(parents=True, exist_ok=True)
        zip_path = directory / f"{basename}.zip"

        printable = [
            part for part in sorted(job.parts.values(), key=lambda p: p.part_num)
            if part.status in (PartStatus.READY, PartStatus.CACHED)
            and part.part_num in geometry_paths
        ]
        total_instances = sum(part.quantity for part in printable)
        instances: dict[str, int] = {}
        written = 0
        seen_names: set[str] = set()

        # ZIP_STORED: STL data compresses poorly and DEFLATE on hundreds of
        # megabytes is the slowest step in the pipeline. Compress the small
        # text members only.
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED,
                             allowZip64=True) as zf:
            # The merged project sits at the top level: it is the single
            # file that opens with every plate already set up.
            if project_file is not None and project_file.exists():
                _write_member(zf, f"{basename}/{project_file.name}", project_file)
                written += 1

            # Pre-arranged plates first: this is the path most people want.
            for index in sorted(plate_files or {}):
                source = plate_files[index]
                if source.exists():
                    _write_member(zf, f"{basename}/Plates/{source.name}", source)
                    written += 1

            for part in (printable if include_stls else []):
                source = geometry_paths[part.part_num]
                if not source.exists():
                    continue
                for index in range(1, part.quantity + 1):
                    name = instance_filename(part.part_num, part.name,
                                             index, part.quantity)
                    name = _deduplicate(name, seen_names)
                    _write_member(zf, f"{basename}/STLs/{name}", source)
                    written += 1
                    if progress and written % 25 == 0:
                        progress(written, total_instances)
                instances[part.part_num] = part.quantity

            if not include_stls:
                # Quantities still belong in the manifest even when the
                # per-piece STLs are not written.
                instances = {p.part_num: p.quantity for p in printable}

            manifest = build_parts_manifest(job, instances)
            zf.writestr(f"{basename}/parts.json",
                        json.dumps(manifest, indent=2),
                        compress_type=zipfile.ZIP_DEFLATED)
            zf.writestr(f"{basename}/README.txt",
                        build_readme(job, written, len(printable), job.failed_parts,
                                     plate_count=len(job.plates),
                                     include_stls=include_stls,
                                     project_name=(project_file.name
                                                   if project_file else None),
                                     separate_plates=len(plate_files or {})),
                        compress_type=zipfile.ZIP_DEFLATED)

        size = zip_path.stat().st_size
        if size > self.max_bytes:
            zip_path.unlink(missing_ok=True)
            raise ValueError(
                f"generated archive is {size} bytes, over the {self.max_bytes} byte limit")
        if progress:
            progress(written, total_instances)
        return ZipResult(zip_path, zip_path.name, size, written + 2, total_instances)

    def cleanup_job(self, job_id: str) -> None:
        try:
            shutil.rmtree(self.job_directory(job_id), ignore_errors=True)
        except ValueError:
            pass

    def purge_older_than(self, seconds: float) -> int:
        """Delete job output directories older than ``seconds``."""
        cutoff = time.time() - seconds
        removed = 0
        if not self.root.is_dir():
            return 0
        for entry in self.root.iterdir():
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
        return removed


def _write_member(zf: zipfile.ZipFile, arcname: str, source: Path) -> None:
    """Stream one file into the archive without loading it into memory."""
    info = zipfile.ZipInfo(arcname, date_time=time.localtime()[:6])
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o644 << 16
    with open(source, "rb") as src, zf.open(info, "w") as dst:
        shutil.copyfileobj(src, dst, COPY_BUFFER)


def _deduplicate(name: str, seen: set[str]) -> str:
    """Guarantee unique archive members even if two parts sanitise alike."""
    if name not in seen:
        seen.add(name)
        return name
    stem, _, suffix = name.rpartition(".")
    counter = 2
    while f"{stem}~{counter}.{suffix}" in seen:
        counter += 1
    unique = f"{stem}~{counter}.{suffix}"
    seen.add(unique)
    return unique

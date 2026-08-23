"""The generation pipeline.

    validate input -> find set -> load inventory -> identify parts
      -> resolve geometry -> convert (concurrently, cached) -> validate
      -> duplicate per quantity -> build ZIP

Everything blocking (SQLite reads, mesh conversion, ZIP writing) runs in a
worker thread so the event loop keeps serving SSE streams while a job runs.

Two rules shape the design:

* **A shape is converted at most once.**  Parts are grouped by resolved
  geometry, so ``3001 x50`` costs one conversion and 50 cheap file copies.
* **One bad part never fails the job.**  Failures are retried with backoff,
  then recorded against that part; the ZIP is still built from everything
  that worked and the job finishes as ``partial``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from pathlib import Path

from ..config import Settings
from ..models.job import Job, JobStatus, STAGE_LABELS, Stage
from ..models.part import ColorCount, GeometryRef, PartStatus, PrintPart
from ..providers.base import ProviderError, ProviderUnavailable, SetNotFound
from ..providers.registry import ProviderRegistry
from ..services.cache_service import CacheService
from ..services.color_service import ColorMode, ColorService, group_key, group_swatch
from ..services.estimate_service import Estimate, PrintProfile, estimate_part
from ..services.job_service import JobManager
from ..services.plate_service import (PackItem, Plate, bed_height, bed_size,
                                      pack_items)
from ..services.threemf_service import (TooManyPlates, plate_filename,
                                        project_filename, write_plate_3mf,
                                        write_project_3mf)
from ..services.zip_service import ZipService

log = logging.getLogger(__name__)

#: Delay between retries of one part, in seconds (exponential backoff).
RETRY_BASE_DELAY = 0.5


class GenerationWorker:
    def __init__(self, settings: Settings, registry: ProviderRegistry,
                 cache: CacheService, zips: ZipService, jobs: JobManager):
        self.settings = settings
        self.registry = registry
        self.cache = cache
        self.zips = zips
        self.jobs = jobs
        self.colors = ColorService(registry.db) if hasattr(registry, "db") else None
        self.print_profile = PrintProfile(
            layer_height_mm=settings.layer_height_mm,
            wall_count=settings.wall_count,
            line_width_mm=settings.line_width_mm,
            infill=settings.infill_percent / 100.0,
            density_g_cm3=settings.filament_density,
            price_per_kg=settings.filament_price_per_kg,
            flow_mm3_s=settings.flow_rate_mm3_s,
            currency=settings.currency,
        )

    # --- helpers ----------------------------------------------------------
    # ``/`` marks these arguments positional-only: event payloads legitimately
    # contain keys named ``job``, ``stage`` and ``part``, which would otherwise
    # collide with the parameter names.
    def _emit(self, job: Job, event_type: str, /, **payload) -> None:
        job.updated_at = time.time()
        self.jobs.publish(job.id, event_type, **payload)

    def _set_stage(self, job: Job, stage: Stage, /, **payload) -> None:
        job.stage = stage
        self._emit(job, "stage", stage=stage.value,
                   label=STAGE_LABELS.get(stage, stage.value), **payload)

    def _part_event(self, job: Job, part: PrintPart, completed: int, total: int, /) -> None:
        self._emit(job, "part_progress", part=part.to_dict(),
                   completed=completed, total=total)

    # --- entry point ------------------------------------------------------
    async def run(self, job: Job) -> None:
        try:
            job.status = JobStatus.RUNNING
            self.jobs.persist(job)
            self._emit(job, "job_started", job=job.summary())
            await asyncio.wait_for(self._pipeline(job), timeout=self.settings.job_timeout)
        except asyncio.TimeoutError:
            self._fail(job, f"Generation timed out after {self.settings.job_timeout}s.")
        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
            job.finished_at = time.time()
            self._emit(job, "job_cancelled", job=job.summary())
            raise
        except (SetNotFound, ProviderUnavailable) as exc:
            self._fail(job, str(exc))
        except Exception as exc:                       # noqa: BLE001 - reported to user
            log.exception("job %s failed", job.id)
            self._fail(job, f"Unexpected error: {exc}")
        finally:
            job.updated_at = time.time()
            self.jobs.persist(job)
            self.jobs.close_channel(job.id)

    def _fail(self, job: Job, message: str) -> None:
        job.status = JobStatus.FAILED
        job.error = message
        job.finished_at = time.time()
        self._emit(job, "job_failed", error=message, job=job.summary())

    # --- pipeline ---------------------------------------------------------
    async def _pipeline(self, job: Job) -> None:
        settings = self.settings

        # 1. find the set ---------------------------------------------------
        self._set_stage(job, Stage.FINDING_SET)
        lego_set = await asyncio.to_thread(self._find_set, job.set_num or job.query)
        job.lego_set = lego_set
        job.set_num = lego_set.set_num
        self._emit(job, "set_found", set=lego_set.to_dict())

        # 2. inventory ------------------------------------------------------
        self._set_stage(job, Stage.LOADING_INVENTORY)
        inventory = await asyncio.to_thread(
            self.registry.part_provider.get_inventory, lego_set, job.include_spares)
        if not inventory.entries:
            raise ProviderError(
                f"No parts inventory is available for set {lego_set.display_number}.")
        if inventory.total_pieces > settings.max_pieces_per_job:
            raise ProviderError(
                f"This set has {inventory.total_pieces} pieces, which is over the "
                f"{settings.max_pieces_per_job}-piece limit for a single job.")
        self._emit(job, "inventory_loaded",
                   entries=len(inventory.entries), total_pieces=inventory.total_pieces)

        # 3. identify parts: collapse colours, keep geometry -----------------
        self._set_stage(job, Stage.IDENTIFYING_PARTS)
        parts = self._collapse_inventory(inventory)
        job.parts = parts
        self._emit(job, "parts_identified", unique_parts=len(parts),
                   total_pieces=sum(p.quantity for p in parts.values()),
                   parts=[p.to_dict() for p in parts.values()])

        # 4. resolve each part to a printable shape --------------------------
        self._set_stage(job, Stage.FINDING_MODELS)
        await asyncio.to_thread(self._resolve_geometries, job)
        groups = self._group_by_geometry(job)
        self._emit(job, "models_resolved",
                   distinct_geometries=len(groups),
                   matched=sum(1 for p in job.parts.values() if p.geometry),
                   unmatched=len(job.failed_parts),
                   parts=[p.to_dict() for p in job.parts.values()])

        # 5. convert every distinct shape once, concurrently ------------------
        self._set_stage(job, Stage.CONVERTING)
        geometry_paths = await self._convert_all(job, groups)

        # 6. arrange the pieces onto build plates -----------------------------
        self._set_stage(job, Stage.ARRANGING)
        plate_files: dict[int, Path] = {}
        project: Path | None = None
        if self.settings.build_plates:
            plates, oversized = await asyncio.to_thread(self._arrange, job, geometry_paths)
            job.plates = plates
            job.oversized = oversized
            job.estimate = self._estimate(job)
            self._emit(job, "estimate_ready", estimate=job.estimate)
            self._emit(job, "plates_ready", plate_count=len(plates),
                       plates=[p.to_dict() for p in plates],
                       oversized=sorted({i.part_num for i in oversized}))
            plate_files, project = await asyncio.to_thread(
                self._write_plates, job, geometry_paths)
            job.project_file = project.name if project else None

        # 7. duplicate per quantity and build the ZIP -------------------------
        self._set_stage(job, Stage.DUPLICATING,
                        files=sum(p.quantity for p in job.parts.values()
                                  if p.part_num in geometry_paths))
        self._set_stage(job, Stage.BUILDING_ZIP)
        result = await asyncio.to_thread(self._build_zip, job, geometry_paths,
                                         plate_files, project)

        job.zip_path = str(result.path)
        job.zip_name = result.name
        job.zip_bytes = result.size_bytes
        job.files_written = result.instance_count
        job.finished_at = time.time()
        job.status = JobStatus.PARTIAL if job.failed_parts else JobStatus.COMPLETE
        self._set_stage(job, Stage.COMPLETE)
        self._emit(job, "job_complete", job=job.summary(),
                   failed_parts=[p.to_dict() for p in job.failed_parts])

    # --- stages -----------------------------------------------------------
    def _find_set(self, set_num: str):
        try:
            return self.registry.set_provider.get_set(set_num)
        except SetNotFound:
            fallback = self.registry.fallback_set_provider
            if fallback is None:
                raise
            log.info("set %s missing locally, trying %s", set_num, fallback.name)
            return fallback.get_set(set_num)

    def _collapse_inventory(self, inventory) -> dict[str, PrintPart]:
        """Merge inventory rows that differ only by colour.

        This is the geometry/appearance split: a red and a blue 2x4 brick are
        the same shape, so they become one part with the summed quantity.
        """
        parts: dict[str, PrintPart] = {}
        provider = self.registry.part_provider
        is_printable = getattr(provider, "is_printable_part", None)

        for entry in inventory.entries:
            if is_printable and not is_printable(entry.part_num, entry.name):
                continue                       # stickers, instructions, cloth
            existing = parts.get(entry.part_num)
            if existing is None:
                existing = PrintPart(
                    part_num=entry.part_num, name=entry.name,
                    quantity=0, img_url=entry.img_url)
                parts[entry.part_num] = existing
            existing.quantity += entry.quantity
            if entry.color_name and entry.color_name not in existing.color_names:
                existing.color_names.append(entry.color_name)
            self._record_color(existing, entry)
        return parts

    def _record_color(self, part: PrintPart, entry) -> None:
        """Keep the per-colour split so plates can be single-filament."""
        color = self.colors.get(entry.color_id) if self.colors else None
        name = entry.color_name or (color.name if color else "Unknown")
        rgb = color.rgb if color else ""
        for existing in part.colors:
            if existing.color_id == entry.color_id:
                existing.quantity += entry.quantity
                return
        part.colors.append(ColorCount(color_id=entry.color_id, color_name=name,
                                      rgb=rgb, quantity=entry.quantity))

    def _resolve_geometries(self, job: Job) -> None:
        """Map each part onto the best available shape, or mark it failed."""
        provider = self.registry.part_provider
        total = len(job.parts)
        for index, part in enumerate(job.parts.values(), start=1):
            match = None
            for candidate, reason in provider.geometry_candidates(part.part_num):
                model_provider = self.registry.find_model_provider(candidate)
                if model_provider is not None:
                    match = GeometryRef(provider=model_provider.name, model_id=candidate,
                                        source_part_num=part.part_num, resolution=reason)
                    break
            if match is None:
                part.status = PartStatus.FAILED
                part.error = "No 3D model is available for this part."
            else:
                part.geometry = match
            self._part_event(job, part, index, total)

    def _group_by_geometry(self, job: Job) -> dict[str, list[PrintPart]]:
        """Group parts sharing one shape so it is converted a single time."""
        groups: dict[str, list[PrintPart]] = defaultdict(list)
        for part in job.parts.values():
            if part.geometry:
                groups[part.geometry.cache_key].append(part)
        return dict(groups)

    async def _convert_all(self, job: Job,
                           groups: dict[str, list[PrintPart]]) -> dict[str, Path]:
        """Convert every distinct shape, at most ``MAX_CONCURRENT_DOWNLOADS`` at a time."""
        semaphore = asyncio.Semaphore(self.settings.max_concurrent_downloads)
        geometry_paths: dict[str, Path] = {}
        total = len(groups)
        done = 0
        lock = asyncio.Lock()

        async def convert(cache_key: str, parts: list[PrintPart]) -> None:
            nonlocal done
            async with semaphore:
                for part in parts:
                    part.status = PartStatus.CONVERTING
                self._emit(job, "part_progress", part=parts[0].to_dict(),
                           completed=done, total=total)
                try:
                    cached = await asyncio.to_thread(self._convert_one, parts[0])
                except Exception as exc:                       # noqa: BLE001
                    message = str(exc) or exc.__class__.__name__
                    for part in parts:
                        part.status = PartStatus.FAILED
                        part.error = message
                    log.warning("part %s failed: %s", parts[0].part_num, message)
                else:
                    for part in parts:
                        part.status = (PartStatus.CACHED if cached.from_cache
                                       else PartStatus.READY)
                        part.triangles = cached.triangles
                        part.dimensions_mm = cached.dimensions
                        part.volume_mm3 = cached.volume_mm3
                        part.area_mm2 = cached.area_mm2
                        geometry_paths[part.part_num] = cached.path
                async with lock:
                    done += 1
                    for part in parts:
                        self._part_event(job, part, done, total)

        await asyncio.gather(*(convert(key, parts) for key, parts in groups.items()))
        return geometry_paths

    def _convert_one(self, part: PrintPart):
        """Build (or fetch from cache) one shape, retrying transient failures."""
        assert part.geometry is not None
        provider = next((p for p in self.registry.available_model_providers()
                         if p.name == part.geometry.provider), None)
        if provider is None:
            raise ProviderUnavailable(
                f"model provider {part.geometry.provider!r} is not available")

        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries):
            try:
                return self.cache.get_or_build(provider, part.geometry.model_id)
            except (ProviderUnavailable, ValueError) as exc:
                # Missing library or invalid geometry will not fix itself.
                raise exc if isinstance(exc, ProviderUnavailable) else exc
            except (ProviderError, OSError) as exc:
                last_error = exc
                if attempt < self.settings.max_retries - 1:
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
        raise last_error or RuntimeError("conversion failed")

    def _build_zip(self, job: Job, geometry_paths: dict[str, Path],
                   plate_files: dict[int, Path] | None = None,
                   project_file: Path | None = None):
        return self.zips.build(
            job, geometry_paths, plate_files=plate_files, project_file=project_file,
            include_stls=self.settings.include_stls,
            progress=lambda written, total: self._emit(
                job, "zip_progress", written=written, total=total))

    def _estimate(self, job: Job) -> dict:
        """Filament, cost and time for everything that will actually print."""
        profile = self.print_profile
        total = Estimate()
        per_plate: dict[int, Estimate] = {}

        for part in job.parts.values():
            if part.status not in (PartStatus.READY, PartStatus.CACHED):
                continue
            height = part.dimensions_mm[2] if part.dimensions_mm else 10.0
            total = total + estimate_part(part.volume_mm3, part.area_mm2,
                                          height, part.quantity, profile)

        # Per-plate figures let someone print the cheap plates first.
        for plate in job.plates:
            running = Estimate()
            for placement in plate.placements:
                part = job.parts.get(placement.item.part_num)
                if part is None:
                    continue
                height = part.dimensions_mm[2] if part.dimensions_mm else 10.0
                running = running + estimate_part(part.volume_mm3, part.area_mm2,
                                                  height, 1, profile)
            per_plate[plate.index] = running

        result = total.to_dict(profile)
        result["profile"] = profile.to_dict()
        result["plates"] = {index: value.to_dict(profile)
                            for index, value in per_plate.items()}
        return result

    # --- build plates -----------------------------------------------------
    def _arrange(self, job: Job, geometry_paths: dict[str, Path]):
        """Turn ready parts into one PackItem per physical piece, then pack.

        Grouping happens here: under "family" or "exact" a plate holds a
        single colour group, so it can be printed without a filament change.
        """
        try:
            mode = ColorMode(job.color_mode)
        except ValueError:
            mode = ColorMode.FAMILY

        items: list[PackItem] = []
        for part in job.parts.values():
            if part.part_num not in geometry_paths or not part.geometry:
                continue
            dimensions = part.dimensions_mm or (10.0, 10.0, 10.0)

            # Walk the colour split so each piece carries its own group. If a
            # part has no colour data, everything falls into one bucket.
            counts = part.colors or [ColorCount(None, "Unknown", "", part.quantity)]
            for count in counts:
                color = self.colors.get(count.color_id) if self.colors else None
                bucket = group_key(color, mode)
                swatch = group_swatch(color, mode) or count.rgb
                for _ in range(count.quantity):
                    items.append(PackItem(
                        part_num=part.part_num, name=part.name,
                        geometry_key=part.geometry.cache_key,
                        width=dimensions[0], depth=dimensions[1], height=dimensions[2],
                        color_name=count.color_name, color_rgb=swatch, group=bucket))

        plates, oversized = pack_items(
            items, bed_size(job.bed_preset),
            gap=self.settings.plate_gap_mm, margin=self.settings.plate_margin_mm,
            group_plates=mode is not ColorMode.NONE)

        if len(plates) > self.settings.max_plates:
            log.warning("job %s produced %d plates, capping at %d",
                        job.id, len(plates), self.settings.max_plates)
            plates = plates[:self.settings.max_plates]
        return plates, oversized

    def _write_plates(self, job: Job,
                      geometry_paths: dict[str, Path]) -> tuple[dict[int, Path], Path | None]:
        """Write the plate files for this job.

        Depending on ``plate_output`` that is one 3MF per plate, a single
        project holding every plate, or both. Both is the default: the
        per-plate files are plain core 3MF that any slicer reads, while the
        project file additionally carries Bambu's plate metadata.
        """
        by_key: dict[str, Path] = {}
        for part in job.parts.values():
            if part.geometry and part.part_num in geometry_paths:
                by_key[part.geometry.cache_key] = geometry_paths[part.part_num]

        directory = self.zips.job_directory(job.id) / "plates"
        directory.mkdir(parents=True, exist_ok=True)
        title = job.lego_set.name if job.lego_set else job.query
        mode = job.plate_output or self.settings.plate_output

        written: dict[int, Path] = {}
        if mode in ("separate", "both"):
            for plate in job.plates:
                name = plate_filename(plate, len(job.plates))
                path = directory / name
                try:
                    write_plate_3mf(plate, by_key, path,
                                    title=f"{title} - plate {plate.index}")
                except Exception:                              # noqa: BLE001
                    log.exception("failed to write plate %d for job %s",
                                  plate.index, job.id)
                    continue
                written[plate.index] = path
                self._emit(job, "plate_written", index=plate.index, name=name,
                           total=len(job.plates))

        project: Path | None = None
        if mode in ("project", "both") and job.plates:
            number = job.lego_set.display_number if job.lego_set else "set"
            path = directory / project_filename(number)
            try:
                write_project_3mf(job.plates, by_key, path, title=title,
                                  bed_height_mm=bed_height(job.bed_preset))
                project = path
            except TooManyPlates as exc:
                # Falling back is better than failing: the per-plate files
                # cover the same job without a plate limit.
                log.warning("job %s: %s", job.id, exc)
                self._emit(job, "project_skipped", reason=str(exc))
                if not written:
                    written = self._write_separate_fallback(job, by_key, directory, title)
            except Exception:                                  # noqa: BLE001
                log.exception("failed to write the project file for job %s", job.id)

        return written, project

    def _write_separate_fallback(self, job: Job, by_key: dict[str, Path],
                                 directory: Path, title: str) -> dict[int, Path]:
        """Write per-plate files after the merged project proved impossible."""
        written: dict[int, Path] = {}
        for plate in job.plates:
            path = directory / plate_filename(plate, len(job.plates))
            try:
                write_plate_3mf(plate, by_key, path,
                                title=f"{title} - plate {plate.index}")
            except Exception:                                  # noqa: BLE001
                log.exception("failed to write plate %d", plate.index)
                continue
            written[plate.index] = path
        return written

    # --- retry of failed parts -------------------------------------------
    async def retry_failed(self, job: Job) -> None:
        """Re-run only the parts that failed, then rebuild the ZIP."""
        failed = job.failed_parts
        if not failed:
            return
        job.status = JobStatus.RUNNING
        job.error = None
        for part in failed:
            part.status = PartStatus.QUEUED
            part.error = None
        self._emit(job, "retry_started", parts=[p.part_num for p in failed])

        self._set_stage(job, Stage.FINDING_MODELS)
        await asyncio.to_thread(self._resolve_subset, job, [p.part_num for p in failed])

        self._set_stage(job, Stage.CONVERTING)
        groups = {key: parts for key, parts in self._group_by_geometry(job).items()
                  if any(p.part_num in {f.part_num for f in failed} for p in parts)}
        retry_paths = await self._convert_all(job, groups)

        # Everything already converted stays; re-look-up its cached path.
        geometry_paths = dict(retry_paths)
        for part in job.parts.values():
            if part.part_num in geometry_paths or not part.geometry:
                continue
            if part.status in (PartStatus.READY, PartStatus.CACHED):
                path = self.cache.path_for(part.geometry.provider, part.geometry.model_id)
                if path.exists():
                    geometry_paths[part.part_num] = path

        self._set_stage(job, Stage.BUILDING_ZIP)
        result = await asyncio.to_thread(self._build_zip, job, geometry_paths)
        job.zip_path = str(result.path)
        job.zip_name = result.name
        job.zip_bytes = result.size_bytes
        job.files_written = result.instance_count
        job.finished_at = time.time()
        job.status = JobStatus.PARTIAL if job.failed_parts else JobStatus.COMPLETE
        self._set_stage(job, Stage.COMPLETE)
        self._emit(job, "job_complete", job=job.summary(),
                   failed_parts=[p.to_dict() for p in job.failed_parts])
        self.jobs.persist(job)
        self.jobs.close_channel(job.id)

    def _resolve_subset(self, job: Job, part_nums: list[str]) -> None:
        provider = self.registry.part_provider
        for index, part_num in enumerate(part_nums, start=1):
            part = job.parts.get(part_num)
            if part is None:
                continue
            for candidate, reason in provider.geometry_candidates(part_num):
                model_provider = self.registry.find_model_provider(candidate)
                if model_provider is not None:
                    part.geometry = GeometryRef(
                        provider=model_provider.name, model_id=candidate,
                        source_part_num=part_num, resolution=reason)
                    break
            else:
                part.status = PartStatus.FAILED
                part.error = "No 3D model is available for this part."
            self._part_event(job, part, index, len(part_nums))

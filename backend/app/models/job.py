"""Job model: one generation run."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

from .part import PrintPart
from .set import LegoSet


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"      # finished, but some parts failed
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (JobStatus.COMPLETE, JobStatus.PARTIAL,
                        JobStatus.FAILED, JobStatus.CANCELLED)


class Stage(str, Enum):
    """User-facing pipeline stages, in order."""

    FINDING_SET = "finding_set"
    LOADING_INVENTORY = "loading_inventory"
    IDENTIFYING_PARTS = "identifying_parts"
    FINDING_MODELS = "finding_models"
    CONVERTING = "converting"
    VALIDATING = "validating"
    ARRANGING = "arranging"
    DUPLICATING = "duplicating"
    BUILDING_ZIP = "building_zip"
    COMPLETE = "complete"


STAGE_LABELS: dict[Stage, str] = {
    Stage.FINDING_SET: "Finding LEGO set",
    Stage.LOADING_INVENTORY: "Loading inventory",
    Stage.IDENTIFYING_PARTS: "Identifying parts",
    Stage.FINDING_MODELS: "Finding 3D models",
    Stage.CONVERTING: "Converting geometry",
    Stage.VALIDATING: "Validating STL",
    Stage.ARRANGING: "Arranging build plates",
    Stage.DUPLICATING: "Creating duplicate parts",
    Stage.BUILDING_ZIP: "Building ZIP",
    Stage.COMPLETE: "Complete",
}


def new_job_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass(slots=True)
class Job:
    id: str = field(default_factory=new_job_id)
    query: str = ""
    set_num: str | None = None
    status: JobStatus = JobStatus.PENDING
    stage: Stage = Stage.FINDING_SET
    lego_set: LegoSet | None = None
    parts: dict[str, PrintPart] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None
    zip_path: str | None = None
    zip_name: str | None = None
    zip_bytes: int = 0
    files_written: int = 0
    include_spares: bool = False
    # --- options chosen for this job ---
    color_mode: str = "family"
    bed_preset: str = "bambu_p1"
    # --- results of arranging ---
    plates: list = field(default_factory=list)      # list[Plate]
    oversized: list = field(default_factory=list)   # pieces too big for the bed

    # --- derived counters -------------------------------------------------
    @property
    def unique_parts(self) -> int:
        return len(self.parts)

    @property
    def total_pieces(self) -> int:
        return sum(p.quantity for p in self.parts.values())

    @property
    def ready_parts(self) -> list[PrintPart]:
        from .part import PartStatus
        return [p for p in self.parts.values()
                if p.status in (PartStatus.READY, PartStatus.CACHED)]

    @property
    def failed_parts(self) -> list[PrintPart]:
        from .part import PartStatus
        return [p for p in self.parts.values() if p.status is PartStatus.FAILED]

    @property
    def distinct_geometries(self) -> int:
        return len({p.geometry.cache_key for p in self.parts.values() if p.geometry})

    def summary(self) -> dict:
        return {
            "job_id": self.id,
            "query": self.query,
            "status": self.status.value,
            "stage": self.stage.value,
            "stage_label": STAGE_LABELS.get(self.stage, self.stage.value),
            "set": self.lego_set.to_dict() if self.lego_set else None,
            "unique_parts": self.unique_parts,
            "total_pieces": self.total_pieces,
            "distinct_geometries": self.distinct_geometries,
            "ready": len(self.ready_parts),
            "failed": len(self.failed_parts),
            "files_written": self.files_written,
            "color_mode": self.color_mode,
            "bed_preset": self.bed_preset,
            "plate_count": len(self.plates),
            "plates": [p.to_dict() for p in self.plates],
            "oversized": sorted({i.part_num for i in self.oversized}),
            "zip_name": self.zip_name,
            "zip_bytes": self.zip_bytes,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }

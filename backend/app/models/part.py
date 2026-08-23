"""Part and geometry models.

The central idea of this application is the split between an *inventory
entry* (a part in a colour, possibly decorated) and a *geometry* (the
physical shape that actually gets printed).  Many inventory entries share a
single geometry: a red 2x4 brick, a blue 2x4 brick and a printed 2x4 brick
are all ``3001``-shaped, so we convert that shape exactly once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PartStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    CONVERTING = "converting"
    VALIDATING = "validating"
    READY = "ready"
    CACHED = "cached"
    FAILED = "failed"


@dataclass(slots=True)
class GeometryRef:
    """Identifies a printable shape in a model provider."""

    provider: str          # e.g. "ldraw"
    model_id: str          # e.g. "3001" (LDraw file stem)
    source_part_num: str   # the inventory part this was resolved from
    resolution: str = "direct"   # how we got here: direct/print_parent/...

    @property
    def cache_key(self) -> str:
        return f"{self.provider}:{self.model_id}"


@dataclass(slots=True)
class ColorCount:
    """How many of this part the set needs in one specific colour."""

    color_id: int | None
    color_name: str
    rgb: str
    quantity: int

    def to_dict(self) -> dict:
        return {"color_id": self.color_id, "color_name": self.color_name,
                "rgb": self.rgb, "quantity": self.quantity}


@dataclass(slots=True)
class PrintPart:
    """An inventory part resolved to a geometry, with its total quantity.

    ``quantity`` is the total across every colour, because that is how many
    physical copies get printed. ``colors`` keeps the per-colour split so the
    pieces can be grouped onto single-filament plates.
    """

    part_num: str
    name: str
    quantity: int
    geometry: GeometryRef | None = None
    status: PartStatus = PartStatus.QUEUED
    color_names: list[str] = field(default_factory=list)
    colors: list[ColorCount] = field(default_factory=list)
    img_url: str | None = None
    error: str | None = None
    triangles: int = 0
    dimensions_mm: tuple[float, float, float] | None = None

    def to_dict(self) -> dict:
        return {
            "part_num": self.part_num,
            "name": self.name,
            "quantity": self.quantity,
            "status": self.status.value,
            "geometry_id": self.geometry.model_id if self.geometry else None,
            "provider": self.geometry.provider if self.geometry else None,
            "resolution": self.geometry.resolution if self.geometry else None,
            "img_url": self.img_url,
            "error": self.error,
            "triangles": self.triangles,
            "dimensions_mm": list(self.dimensions_mm) if self.dimensions_mm else None,
            "colors": [c.to_dict() for c in self.colors],
        }

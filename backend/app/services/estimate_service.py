"""Print estimates: filament, cost and time.

These are estimates, not slicer output. The slicer is authoritative — it
knows your profile, your speeds and your printer. What this gives you is a
sense of scale before you commit: is this set 200 g or 2 kg, an evening or a
fortnight.

How the filament figure is reached
----------------------------------
Every part's true solid volume ``V`` and surface area ``A`` come from its
mesh. From those, a characteristic wall thickness falls out::

    t = 2V / A

For a thin-walled shape this is the wall thickness, which is exactly what
LEGO elements are: measured on a 2x4 brick it returns 1.50 mm, matching the
real moulding. That gives an honest way to decide how much of a part is
perimeter and how much is infill:

* if ``t`` is no thicker than the printed wall, the part is solid perimeter;
* otherwise the walls take ``wall/t`` of the volume and infill fills the rest.

Time is the rough one. Extrusion time follows from volume and flow rate, but
the real cost on a plate of small parts is travel between objects and the
per-layer overhead, which depends on the slicer and machine. The constants
are configurable so a single real slice lets you calibrate them.
"""
from __future__ import annotations

from dataclasses import dataclass

#: PLA. PETG is ~1.27, ABS ~1.04.
DEFAULT_DENSITY_G_CM3 = 1.24
DEFAULT_PRICE_PER_KG = 20.0
DEFAULT_LAYER_HEIGHT_MM = 0.2
DEFAULT_WALL_COUNT = 2
DEFAULT_LINE_WIDTH_MM = 0.42
DEFAULT_INFILL = 0.15
#: Volumetric flow actually achieved, averaged over a print. Deliberately
#: conservative: peak flow is higher but is not sustained on small parts.
DEFAULT_FLOW_MM3_S = 8.0
#: Fixed cost per object: priming, first layer care, seam changes.
DEFAULT_PER_OBJECT_SECONDS = 15.0
#: Travel and acceleration cost of visiting one object once per layer.
DEFAULT_TRAVEL_SECONDS = 0.35


@dataclass(frozen=True, slots=True)
class PrintProfile:
    """Everything the estimate depends on, in one place."""

    layer_height_mm: float = DEFAULT_LAYER_HEIGHT_MM
    wall_count: int = DEFAULT_WALL_COUNT
    line_width_mm: float = DEFAULT_LINE_WIDTH_MM
    infill: float = DEFAULT_INFILL
    density_g_cm3: float = DEFAULT_DENSITY_G_CM3
    price_per_kg: float = DEFAULT_PRICE_PER_KG
    flow_mm3_s: float = DEFAULT_FLOW_MM3_S
    per_object_seconds: float = DEFAULT_PER_OBJECT_SECONDS
    travel_seconds: float = DEFAULT_TRAVEL_SECONDS
    currency: str = "£"

    @property
    def wall_thickness_mm(self) -> float:
        return max(self.wall_count, 1) * self.line_width_mm

    def to_dict(self) -> dict:
        return {
            "layer_height_mm": self.layer_height_mm,
            "wall_count": self.wall_count,
            "infill_percent": round(self.infill * 100),
            "density_g_cm3": self.density_g_cm3,
            "price_per_kg": self.price_per_kg,
            "currency": self.currency,
        }


@dataclass(slots=True)
class Estimate:
    """What a set, a plate or a single part will cost to print."""

    material_mm3: float = 0.0
    seconds: float = 0.0
    pieces: int = 0

    def __add__(self, other: "Estimate") -> "Estimate":
        return Estimate(self.material_mm3 + other.material_mm3,
                        self.seconds + other.seconds,
                        self.pieces + other.pieces)

    def grams(self, profile: PrintProfile) -> float:
        # mm3 -> cm3 -> grams
        return self.material_mm3 / 1000.0 * profile.density_g_cm3

    def metres(self, profile: PrintProfile, diameter_mm: float = 1.75) -> float:
        area = 3.141592653589793 * (diameter_mm / 2.0) ** 2
        return (self.material_mm3 / area) / 1000.0 if area else 0.0

    def cost(self, profile: PrintProfile) -> float:
        return self.grams(profile) / 1000.0 * profile.price_per_kg

    def to_dict(self, profile: PrintProfile) -> dict:
        grams = self.grams(profile)
        return {
            "pieces": self.pieces,
            "grams": round(grams, 1),
            "kilograms": round(grams / 1000.0, 3),
            "metres": round(self.metres(profile), 1),
            "spools": round(grams / 1000.0, 2),
            "hours": round(self.seconds / 3600.0, 1),
            "seconds": round(self.seconds),
            "cost": round(self.cost(profile), 2),
            "currency": profile.currency,
            "time_text": format_duration(self.seconds),
        }


def format_duration(seconds: float) -> str:
    """"3 d 4 h", "6 h 20 m", "45 m"."""
    seconds = max(0.0, seconds)
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} m" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def solid_fraction(volume_mm3: float, area_mm2: float,
                   profile: PrintProfile) -> float:
    """Share of a part's volume that ends up as filament.

    Uses the part's own wall thickness (``2V/A``) to split it into perimeter
    and infill, so a chunky part is mostly infill while a thin one — which
    is most LEGO elements — comes out solid.
    """
    if volume_mm3 <= 0 or area_mm2 <= 0:
        return 1.0
    thickness = 2.0 * volume_mm3 / area_mm2
    wall = profile.wall_thickness_mm
    if thickness <= wall:
        return 1.0
    wall_share = wall / thickness
    return min(1.0, wall_share + profile.infill * (1.0 - wall_share))


def estimate_part(volume_mm3: float, area_mm2: float, height_mm: float,
                  quantity: int = 1,
                  profile: PrintProfile | None = None) -> Estimate:
    """Estimate one part, times ``quantity`` copies."""
    profile = profile or PrintProfile()
    quantity = max(int(quantity), 0)
    if quantity == 0 or volume_mm3 <= 0:
        return Estimate(0.0, 0.0, 0)

    material = volume_mm3 * solid_fraction(volume_mm3, area_mm2, profile)

    layers = max(1.0, height_mm / max(profile.layer_height_mm, 1e-6))
    extrusion = material / max(profile.flow_mm3_s, 1e-6)
    overhead = profile.per_object_seconds + layers * profile.travel_seconds
    per_piece = extrusion + overhead

    return Estimate(material_mm3=material * quantity,
                    seconds=per_piece * quantity,
                    pieces=quantity)


def estimate_total(parts: list[tuple[float, float, float, int]],
                   profile: PrintProfile | None = None) -> Estimate:
    """Sum estimates over ``(volume, area, height, quantity)`` tuples."""
    profile = profile or PrintProfile()
    total = Estimate()
    for volume, area, height, quantity in parts:
        total = total + estimate_part(volume, area, height, quantity, profile)
    return total

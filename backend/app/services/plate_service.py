"""Arranging parts onto build plates.

The problem this solves: a large set is 800+ individual pieces. Importing
that many objects at once is slow or impossible in a slicer, and its
auto-arrange then has to solve the packing itself — which is where it gives
up and leaves objects off the bed.

So the packing happens here instead, and the output is a handful of plates
that are already full and already valid.

The algorithm is shelf packing (first-fit decreasing height) with optional
90-degree rotation: parts are sorted tallest-footprint first and laid in
rows. It is not optimal — optimal 2D bin packing is NP-hard — but it is fast,
predictable, and on real LEGO inventories reaches a good fill because the
pieces are small relative to the bed and highly repetitive.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Common printers, so the UI can offer names rather than numbers.
#: Values are usable print area in millimetres (width, depth).
BED_PRESETS: dict[str, tuple[float, float]] = {
    "bambu_a1_mini": (180.0, 180.0),
    "bambu_a1": (256.0, 256.0),
    "bambu_p1": (256.0, 256.0),
    "bambu_x1": (256.0, 256.0),
    "bambu_h2d": (325.0, 320.0),
    "prusa_mk4": (250.0, 210.0),
    "ender_3": (220.0, 220.0),
    "voron_350": (350.0, 350.0),
}

DEFAULT_BED = "bambu_p1"

#: Gap left between parts, in millimetres. Slicers need a little clearance
#: to place brims and to keep objects from being merged.
DEFAULT_GAP_MM = 3.0

#: Margin kept clear at the edge of the bed.
DEFAULT_MARGIN_MM = 5.0


def bed_size(preset: str) -> tuple[float, float]:
    return BED_PRESETS.get(preset, BED_PRESETS[DEFAULT_BED])


@dataclass(slots=True)
class PackItem:
    """One physical piece waiting for a place on a plate."""

    part_num: str
    name: str
    geometry_key: str          # which cached mesh this instance uses
    width: float               # footprint in mm, before rotation
    depth: float
    height: float
    color_name: str | None = None
    color_rgb: str | None = None
    group: str = ""            # colour-group bucket, "" when ungrouped


@dataclass(slots=True)
class Placement:
    item: PackItem
    x: float                   # centre position on the bed, mm
    y: float
    rotated: bool = False      # rotated 90 degrees about Z

    @property
    def width(self) -> float:
        return self.item.depth if self.rotated else self.item.width

    @property
    def depth(self) -> float:
        return self.item.width if self.rotated else self.item.depth


@dataclass(slots=True)
class Plate:
    index: int                 # 1-based
    group: str = ""
    placements: list[Placement] = field(default_factory=list)
    bed_width: float = 256.0
    bed_depth: float = 256.0

    @property
    def count(self) -> int:
        return len(self.placements)

    @property
    def used_area(self) -> float:
        return sum(p.width * p.depth for p in self.placements)

    @property
    def fill_percent(self) -> float:
        total = self.bed_width * self.bed_depth
        return 100.0 * self.used_area / total if total else 0.0

    @property
    def swatch(self) -> str | None:
        for placement in self.placements:
            if placement.item.color_rgb:
                return placement.item.color_rgb
        return None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "group": self.group,
            "count": self.count,
            "fill_percent": round(self.fill_percent, 1),
            "bed": [self.bed_width, self.bed_depth],
            "parts": sorted({p.item.part_num for p in self.placements}),
        }


class PartTooLarge(ValueError):
    """A single piece does not fit the configured bed at all."""


def pack_items(items: list[PackItem], bed: tuple[float, float],
               *, gap: float = DEFAULT_GAP_MM, margin: float = DEFAULT_MARGIN_MM,
               allow_rotation: bool = True,
               group_plates: bool = True) -> tuple[list[Plate], list[PackItem]]:
    """Lay ``items`` out onto as few plates as reasonably possible.

    Returns ``(plates, oversized)``. Anything in *oversized* is physically
    bigger than the bed and cannot be printed on this printer; it is
    reported rather than silently dropped.

    When ``group_plates`` is set, a plate holds only one colour group, so a
    plate can be printed in a single filament without swaps.
    """
    bed_width, bed_depth = bed
    usable_width = bed_width - 2 * margin
    usable_depth = bed_depth - 2 * margin
    if usable_width <= 0 or usable_depth <= 0:
        raise ValueError("bed is smaller than its margins")

    oversized: list[PackItem] = []
    packable: list[PackItem] = []
    for item in items:
        fits = (item.width <= usable_width and item.depth <= usable_depth)
        fits_rotated = allow_rotation and (item.depth <= usable_width
                                           and item.width <= usable_depth)
        (packable if (fits or fits_rotated) else oversized).append(item)

    buckets: dict[str, list[PackItem]] = {}
    for item in packable:
        buckets.setdefault(item.group if group_plates else "", []).append(item)

    from .color_service import sort_key

    plates: list[Plate] = []
    for group in sorted(buckets, key=sort_key):
        plates.extend(_pack_group(buckets[group], group, usable_width, usable_depth,
                                  gap, margin, bed_width, bed_depth, allow_rotation))

    for number, plate in enumerate(plates, start=1):
        plate.index = number
    return plates, oversized


def _pack_group(items: list[PackItem], group: str,
                usable_width: float, usable_depth: float,
                gap: float, margin: float,
                bed_width: float, bed_depth: float,
                allow_rotation: bool) -> list[Plate]:
    """Shelf-pack one colour group into as many plates as it needs."""

    def footprint(item: PackItem) -> tuple[float, float, bool]:
        """Pick an orientation that fits, preferring the shorter shelf.

        Fitting comes first: a part longer than the bed is wide has to be
        turned, whatever that does to the shelf height. Only when both
        orientations are viable do we choose the one that keeps the row
        short, which is what makes shelf packing efficient.
        """
        upright_fits = item.width <= usable_width and item.depth <= usable_depth
        turned_fits = (allow_rotation and item.depth <= usable_width
                       and item.width <= usable_depth)

        if turned_fits and not upright_fits:
            return item.depth, item.width, True
        if upright_fits and not turned_fits:
            return item.width, item.depth, False
        # Either orientation works (or neither does): keep the shelf shallow.
        if allow_rotation and item.depth > item.width:
            return item.depth, item.width, True
        return item.width, item.depth, False

    # Tallest shelf-height first: classic first-fit decreasing height.
    ordered = sorted(items, key=lambda i: (-footprint(i)[1], -footprint(i)[0]))

    plates: list[Plate] = []
    plate: Plate | None = None
    shelf_y = 0.0          # bottom edge of the current shelf
    shelf_height = 0.0
    cursor_x = 0.0

    def new_plate() -> Plate:
        nonlocal shelf_y, shelf_height, cursor_x
        shelf_y = 0.0
        shelf_height = 0.0
        cursor_x = 0.0
        created = Plate(index=len(plates) + 1, group=group,
                        bed_width=bed_width, bed_depth=bed_depth)
        plates.append(created)
        return created

    for item in ordered:
        width, depth, rotated = footprint(item)

        if plate is None:
            plate = new_plate()

        # Start a new shelf when this row is full.
        if cursor_x > 0 and cursor_x + width > usable_width:
            shelf_y += shelf_height + gap
            shelf_height = 0.0
            cursor_x = 0.0

        # Start a new plate when the shelf would run off the back.
        if shelf_y + depth > usable_depth:
            plate = new_plate()

        # Convert the bottom-left corner into a centre point, which is what
        # the 3MF writer and every slicer expect for object placement.
        x = margin + cursor_x + width / 2.0
        y = margin + shelf_y + depth / 2.0
        plate.placements.append(Placement(item=item, x=x, y=y, rotated=rotated))

        cursor_x += width + gap
        shelf_height = max(shelf_height, depth)

    return plates


def summarise(plates: list[Plate]) -> dict:
    return {
        "plate_count": len(plates),
        "piece_count": sum(p.count for p in plates),
        "average_fill": round(
            sum(p.fill_percent for p in plates) / len(plates), 1) if plates else 0.0,
        "plates": [p.to_dict() for p in plates],
    }

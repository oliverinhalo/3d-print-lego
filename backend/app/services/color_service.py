"""Colour handling: exact LEGO colours, and grouping into printable families.

A printer loads one filament at a time, so the useful question is not "what
colour is this brick" but "which pile of bricks can I print together". Three
modes answer that:

``none``    ignore colour entirely — pack purely by size (fewest plates)
``family``  group similar colours — every red, dark red and reddish brown
            lands on the same plate, so one red filament prints them all
``exact``   one group per exact LEGO colour code — the faithful option, at
            the cost of more, emptier plates

Families are derived from each colour's RGB rather than from its name:
Rebrickable has 275 colours with names like "Dark Bluish Gray" and "Medium
Nougat", and hue is a far more reliable signal than string matching.
"""
from __future__ import annotations

import colorsys
from dataclasses import dataclass
from enum import Enum

from ..db import Database


class ColorMode(str, Enum):
    NONE = "none"
    FAMILY = "family"
    EXACT = "exact"


#: Display order for families, roughly rainbow then neutrals.
FAMILY_ORDER = [
    "Red", "Orange", "Yellow", "Green", "Blue", "Purple", "Pink",
    "Brown", "Tan", "White", "Light Grey", "Dark Grey", "Black",
    "Transparent", "Other",
]

#: Representative swatch per family, for the UI.
FAMILY_SWATCH = {
    "Red": "C91A09", "Orange": "FE8A18", "Yellow": "F2CD37",
    "Green": "237841", "Blue": "0055BF", "Purple": "81007B",
    "Pink": "C870A0", "Brown": "583927", "Tan": "E4CD9E",
    "White": "FFFFFF", "Light Grey": "9BA19D", "Dark Grey": "6D6E5C",
    "Black": "05131D", "Transparent": "C0C0C0", "Other": "8A8A8A",
}


@dataclass(slots=True)
class LegoColor:
    id: int
    name: str
    rgb: str
    is_trans: bool

    @property
    def family(self) -> str:
        return family_for(self.rgb, self.is_trans)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "rgb": self.rgb,
                "is_trans": self.is_trans, "family": self.family}


def _to_hsv(rgb: str) -> tuple[float, float, float]:
    text = (rgb or "").strip().lstrip("#")
    if len(text) != 6:
        return (0.0, 0.0, 0.5)
    try:
        r, g, b = (int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (0.0, 0.0, 0.5)
    return colorsys.rgb_to_hsv(r, g, b)


def family_for(rgb: str, is_trans: bool = False) -> str:
    """Map an RGB hex string onto a printable colour family.

    Transparent colours are their own family: they need translucent
    filament, so they cannot share a plate with opaque parts of the same hue.
    """
    if is_trans:
        return "Transparent"

    hue, saturation, value = _to_hsv(rgb)
    degrees = hue * 360.0

    # Very dark colours are black whatever their hue says. LEGO black is
    # #05131D — a near-black navy whose measured saturation is 0.83, so a
    # saturation test alone would file it under Blue.
    if value < 0.18:
        return "Black"

    # Neutrals: hue is meaningless without saturation. Between 0.10 and 0.25
    # the answer depends on lightness — a muted mid tone is grey, while a
    # bright one is a pastel tint of its hue (Light Green, Lavender).
    if saturation < 0.10 or (saturation < 0.25 and value <= 0.75):
        if value < 0.30:
            return "Black"
        if value < 0.58:
            return "Dark Grey"
        if value < 0.86:
            return "Light Grey"
        return "White"

    # Dark warm tones read as brown, not as dim orange.
    if 10 <= degrees < 50 and value < 0.50:
        return "Brown"
    # Nougat, tan and the skin tones: warm but neither vivid nor dark.
    if 15 <= degrees < 60 and saturation < 0.60 and value > 0.55:
        return "Tan"

    if degrees < 12 or degrees >= 340:
        # Washed-out reds are pink; vivid ones are red.
        if saturation < 0.55 and value > 0.70:
            return "Pink"
        return "Red"
    if degrees < 42:
        return "Orange"
    if degrees < 70:
        return "Yellow"
    if degrees < 170:
        return "Green"
    if degrees < 265:
        return "Blue"
    if degrees < 320:
        return "Purple"
    return "Pink"


def group_key(color: LegoColor | None, mode: ColorMode) -> str:
    """The grouping bucket a colour belongs to under ``mode``."""
    if mode is ColorMode.NONE or color is None:
        return ""
    if mode is ColorMode.EXACT:
        return color.name or f"Colour {color.id}"
    return color.family


def group_swatch(color: LegoColor | None, mode: ColorMode) -> str | None:
    if mode is ColorMode.NONE or color is None:
        return None
    if mode is ColorMode.EXACT:
        return color.rgb
    return FAMILY_SWATCH.get(color.family, "8A8A8A")


def sort_key(group: str) -> tuple[int, str]:
    """Order groups sensibly: families in rainbow order, then alphabetical."""
    if group in FAMILY_ORDER:
        return (FAMILY_ORDER.index(group), "")
    return (len(FAMILY_ORDER), group)


class ColorService:
    """Reads the colour table once and answers lookups from memory."""

    def __init__(self, db: Database):
        self.db = db
        self._colors: dict[int, LegoColor] | None = None

    @property
    def colors(self) -> dict[int, LegoColor]:
        if self._colors is None:
            self._colors = {}
            try:
                rows = self.db.query("SELECT id, name, rgb, is_trans FROM colors")
            except Exception:                                  # noqa: BLE001
                rows = []
            for row in rows:
                self._colors[int(row["id"])] = LegoColor(
                    id=int(row["id"]), name=row["name"] or "Unknown",
                    rgb=(row["rgb"] or "").strip(),
                    is_trans=str(row["is_trans"]).strip().lower() in ("true", "t", "1"))
        return self._colors

    def get(self, color_id: int | None) -> LegoColor | None:
        if color_id is None:
            return None
        return self.colors.get(int(color_id))

    def families(self) -> dict[str, list[LegoColor]]:
        grouped: dict[str, list[LegoColor]] = {}
        for color in self.colors.values():
            grouped.setdefault(color.family, []).append(color)
        return grouped


# --- limiting how many colours you actually have to print ------------------
#
# A set can easily need a dozen filament colours. Most people have far fewer,
# so groups are merged until only ``max_groups`` remain — always merging the
# two that look most alike, so what you lose is the distinction between
# tan and nougat rather than between red and blue.


def _srgb_to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def to_lab(rgb: str) -> tuple[float, float, float]:
    """Convert an sRGB hex string to CIELAB.

    Distances in Lab track how different two colours *look*, which plain RGB
    distance does not: #000080 and #008000 are equally far apart in RGB, but
    nobody would confuse navy with green.
    """
    text = (rgb or "").strip().lstrip("#")
    if len(text) != 6:
        return (50.0, 0.0, 0.0)
    try:
        r, g, b = (int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (50.0, 0.0, 0.0)

    r, g, b = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
    # sRGB D65 -> XYZ
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = (r * 0.2126 + g * 0.7152 + b * 0.0722)
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t) + (16 / 116)

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _lab_distance(first: tuple[float, float, float],
                  second: tuple[float, float, float]) -> float:
    return ((first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2
            + (first[2] - second[2]) ** 2) ** 0.5


def color_distance(first: str, second: str) -> float:
    """Perceptual distance between two hex colours (CIE76 delta-E)."""
    return _lab_distance(to_lab(first), to_lab(second))


#: Added to the distance between a transparent group and an opaque one.
#: Translucent filament is not interchangeable with solid, so these merge
#: only when the colour budget leaves no alternative.
TRANSPARENT_PENALTY = 200.0


@dataclass(slots=True)
class ColorGroup:
    """One filament colour: what it is called, its swatch, and how much of it."""

    name: str
    rgb: str
    pieces: int
    members: list[str] = None  # type: ignore[assignment]
    #: Piece-weighted centroid in Lab. Kept alongside the swatch so that a
    #: merged group is compared by what it now contains rather than by
    #: whichever member happened to name it — without this, greys chain into
    #: white and drag unrelated colours along with them.
    centroid: tuple[float, float, float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.members is None:
            self.members = [self.name]
        if self.centroid is None:
            self.centroid = to_lab(self.rgb)

    @property
    def is_transparent(self) -> bool:
        return self.name == "Transparent" or "Trans" in self.members[0]

    def to_dict(self) -> dict:
        return {"name": self.name, "rgb": self.rgb, "pieces": self.pieces,
                "members": sorted(self.members)}


def merge_to_limit(groups: list[ColorGroup],
                   max_groups: int) -> tuple[list[ColorGroup], dict[str, str]]:
    """Merge the most similar groups until at most ``max_groups`` remain.

    Returns the surviving groups and a mapping from every original group name
    to the one it ended up in.

    Merging is greedy, and the cost of a merge is *how wrong it looks in
    total*: the perceptual distance multiplied by the number of pieces that
    would change colour. Distance alone gets this badly wrong — it will
    happily fold 38 blue pieces into black because the two are nominally
    close, while leaving two stray purple bricks on a plate of their own.
    Weighting by piece count absorbs the strays first, which is what you
    actually want when the goal is "I only own four colours".

    The surviving group keeps the name and swatch of whichever side has more
    pieces, so the filament you load is the one most of those parts need.
    """
    if max_groups <= 0 or len(groups) <= max_groups:
        return list(groups), {g.name: g.name for g in groups}

    surviving = [ColorGroup(g.name, g.rgb, g.pieces, list(g.members), g.centroid)
                 for g in groups]
    mapping = {g.name: g.name for g in groups}

    while len(surviving) > max_groups:
        best: tuple[float, int, int] | None = None
        for i in range(len(surviving)):
            for j in range(i + 1, len(surviving)):
                first, second = surviving[i], surviving[j]
                distance = _lab_distance(first.centroid, second.centroid)
                if first.is_transparent != second.is_transparent:
                    distance += TRANSPARENT_PENALTY
                # Only the smaller pile actually changes colour, so that is
                # what the mistake costs.
                recoloured = min(first.pieces, second.pieces)
                cost = distance * max(recoloured, 1)
                if best is None or cost < best[0]:
                    best = (cost, i, j)
        if best is None:
            break

        _distance, i, j = best
        first, second = surviving[i], surviving[j]
        # The bigger pile keeps its identity; ties go to the earlier group so
        # the outcome does not depend on dictionary ordering.
        keeper, absorbed = ((first, second) if first.pieces >= second.pieces
                            else (second, first))
        total = keeper.pieces + absorbed.pieces
        keeper.centroid = tuple(
            (k * keeper.pieces + a * absorbed.pieces) / max(total, 1)
            for k, a in zip(keeper.centroid, absorbed.centroid))  # type: ignore[assignment]
        keeper.pieces = total
        keeper.members.extend(absorbed.members)
        surviving.remove(absorbed)

        # Everything that already pointed at the absorbed group follows it.
        for original, current in mapping.items():
            if current in absorbed.members or current == absorbed.name:
                mapping[original] = keeper.name

    return surviving, mapping

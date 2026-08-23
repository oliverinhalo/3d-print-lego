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

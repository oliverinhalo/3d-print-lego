"""Domain models describing LEGO sets and their inventories."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class LegoSet:
    """A LEGO set as reported by a :class:`SetProvider`."""

    set_num: str            # canonical inventory id, e.g. "77263-1"
    name: str
    year: int | None = None
    theme: str | None = None
    num_parts: int = 0
    img_url: str | None = None

    @property
    def display_number(self) -> str:
        """The number a human recognises: "77263-1" -> "77263"."""
        return self.set_num.split("-", 1)[0]

    def to_dict(self) -> dict:
        return {
            "set_num": self.set_num,
            "display_number": self.display_number,
            "name": self.name,
            "year": self.year,
            "theme": self.theme,
            "num_parts": self.num_parts,
            "img_url": self.img_url,
        }


@dataclass(slots=True)
class InventoryEntry:
    """One row of a set inventory: a part in a colour, with a quantity."""

    part_num: str
    name: str
    quantity: int
    color_id: int | None = None
    color_name: str | None = None
    is_spare: bool = False
    img_url: str | None = None


@dataclass(slots=True)
class SetInventory:
    lego_set: LegoSet
    entries: list[InventoryEntry] = field(default_factory=list)

    @property
    def total_pieces(self) -> int:
        return sum(e.quantity for e in self.entries)

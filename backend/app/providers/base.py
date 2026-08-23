"""Provider interfaces.

The application deliberately talks to data sources only through these four
abstractions, so a source can be swapped without touching the pipeline:

``SetProvider``    find a set and describe it
``PartProvider``   list a set's inventory and resolve a part to a *geometry*
``ModelProvider``  turn a geometry reference into a mesh / STL on disk
``STLProvider``    a ModelProvider that already serves finished STL bytes

See ``docs/PROVIDERS.md`` for what each shipped implementation can do.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..models.part import GeometryRef
from ..models.set import LegoSet, SetInventory


class ProviderError(RuntimeError):
    """A provider failed in a way the pipeline can report to the user."""


class ProviderUnavailable(ProviderError):
    """The provider is configured but its data/credentials are missing."""


class SetNotFound(ProviderError):
    """The requested set does not exist in this provider's data."""


class SetProvider(ABC):
    """Finds LEGO sets and returns their descriptive metadata."""

    name: str = "set"

    @abstractmethod
    def get_set(self, set_num: str) -> LegoSet:
        """Return the set identified by a canonical ``<num>-<version>`` id."""

    @abstractmethod
    def search(self, text: str, limit: int = 10) -> list[LegoSet]:
        """Free-text search, used for 'did you mean' suggestions."""

    def health(self) -> dict:
        return {"name": self.name, "available": True}


class PartProvider(ABC):
    """Lists a set's inventory and maps parts onto printable geometry."""

    name: str = "part"

    @abstractmethod
    def get_inventory(self, lego_set: LegoSet, include_spares: bool = False) -> SetInventory:
        """Return every part in the set with quantities."""

    @abstractmethod
    def geometry_candidates(self, part_num: str) -> list[tuple[str, str]]:
        """Candidate geometry ids for a part, best first.

        Returns ``(candidate_id, reason)`` pairs, where *reason* explains the
        relationship (``direct``, ``print_parent``, ``mould_variant`` ...).
        This is where the geometry/appearance split lives: a printed tile
        yields its undecorated parent, because they share a shape.
        """

    def health(self) -> dict:
        return {"name": self.name, "available": True}


class ModelProvider(ABC):
    """Turns a geometry id into an STL file on disk."""

    name: str = "model"

    @abstractmethod
    def has_model(self, model_id: str) -> bool:
        """Cheap existence check used during part matching."""

    @abstractmethod
    def build_stl(self, model_id: str, destination: Path) -> "ModelResult":
        """Produce a validated STL at ``destination``."""

    @property
    @abstractmethod
    def source_version(self) -> str:
        """Version of the underlying model data, for cache invalidation."""

    @property
    def converter_version(self) -> str:
        """Bumped whenever conversion output changes; invalidates the cache."""
        return "1"

    def describe(self, model_id: str) -> str | None:
        """Human-readable title for a model, when the source has one."""
        return None

    def health(self) -> dict:
        return {"name": self.name, "available": True}


class ModelResult:
    """Outcome of building one STL."""

    __slots__ = ("model_id", "path", "size_bytes", "triangles", "dimensions",
                 "warnings", "source_version", "volume_mm3", "area_mm2")

    def __init__(self, model_id: str, path: Path, size_bytes: int, triangles: int,
                 dimensions: tuple[float, float, float], warnings: list[str],
                 source_version: str, volume_mm3: float = 0.0,
                 area_mm2: float = 0.0):
        self.model_id = model_id
        self.path = path
        self.size_bytes = size_bytes
        self.triangles = triangles
        self.dimensions = dimensions
        self.warnings = warnings
        self.source_version = source_version
        # Solid volume and surface area, used for filament estimates.
        self.volume_mm3 = volume_mm3
        self.area_mm2 = area_mm2


class STLProvider(ModelProvider):
    """A model provider whose source is already STL; no conversion needed."""

    name = "stl"

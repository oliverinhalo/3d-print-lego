"""Shared fixtures.

The suite runs entirely against synthetic providers and a temporary
database, so it needs neither the 145 MB LDraw library nor the catalogue
download.  Tests that do want the real data are marked ``realdata`` and skip
themselves when it is absent.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.ldraw.mesh import Mesh  # noqa: E402
from app.models.set import InventoryEntry, LegoSet, SetInventory  # noqa: E402
from app.providers.base import (ModelProvider, ModelResult, PartProvider,  # noqa: E402
                                ProviderError, SetNotFound, SetProvider)


def pytest_configure(config):
    config.addinivalue_line("markers",
                            "realdata: needs the downloaded LDraw/Rebrickable data")


# --- synthetic geometry ---------------------------------------------------

def make_cube_mesh(size: float = 10.0) -> Mesh:
    """A closed, correctly wound cube — a stand-in for a real part."""
    s = size
    v = [(0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0),
         (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)]
    faces = [
        (0, 3, 2), (0, 2, 1),      # bottom
        (4, 5, 6), (4, 6, 7),      # top
        (0, 1, 5), (0, 5, 4),      # front
        (2, 3, 7), (2, 7, 6),      # back
        (1, 2, 6), (1, 6, 5),      # right
        (3, 0, 4), (3, 4, 7),      # left
    ]
    return Mesh([(v[a], v[b], v[c]) for a, b, c in faces])


def write_cube_stl(path: Path, size: float = 10.0) -> Path:
    from app.ldraw.stl import write_binary_stl
    write_binary_stl(make_cube_mesh(size), path, header="test cube")
    return path


@pytest.fixture
def cube_stl(tmp_path: Path) -> Path:
    return write_cube_stl(tmp_path / "cube.stl")


# --- settings / database --------------------------------------------------

@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.sqlite3'}",
        source_directory=tmp_path / "sources",
        cache_directory=tmp_path / "cache",
        output_directory=tmp_path / "jobs",
        max_concurrent_downloads=4,
        max_retries=2,
        job_timeout=60,
    )


@pytest.fixture
def db(settings: Settings) -> Database:
    settings.ensure_directories()
    return Database(settings.sqlite_path)


# --- fake providers -------------------------------------------------------

class FakeSetProvider(SetProvider, PartProvider):
    """An in-memory catalogue with a controllable inventory."""

    name = "fake_set"

    def __init__(self, inventory: dict[str, int] | None = None,
                 names: dict[str, str] | None = None):
        self.inventory = inventory or {"3001": 2, "3024": 3}
        self.names = names or {}
        self.inventory_calls = 0

    def get_set(self, set_num: str) -> LegoSet:
        if set_num.startswith("99999"):
            raise SetNotFound(f"LEGO set {set_num} was not found in the catalogue.")
        return LegoSet(set_num=set_num, name="Test Set", year=2024,
                       theme="Test", num_parts=sum(self.inventory.values()))

    def search(self, text: str, limit: int = 10) -> list[LegoSet]:
        return [self.get_set("12345-1")]

    def get_inventory(self, lego_set: LegoSet, include_spares: bool = False) -> SetInventory:
        self.inventory_calls += 1
        entries = [
            InventoryEntry(part_num=part, name=self.names.get(part, f"Part {part}"),
                           quantity=qty, color_id=4, color_name="Red")
            for part, qty in self.inventory.items()
        ]
        return SetInventory(lego_set=lego_set, entries=entries)

    def geometry_candidates(self, part_num: str) -> list[tuple[str, str]]:
        candidates = [(part_num, "direct")]
        if part_num.endswith("pr0001"):
            candidates.append((part_num[:-6], "print_parent"))
        return candidates

    def is_printable_part(self, part_num: str, name: str) -> bool:
        return "sticker" not in (name or "").lower()


class FakeModelProvider(ModelProvider):
    """Builds cube STLs, and can be told to fail specific parts."""

    name = "fake_model"

    def __init__(self, known: set[str] | None = None,
                 failing: set[str] | None = None,
                 fail_times: dict[str, int] | None = None):
        self.known = known if known is not None else {"3001", "3024", "3673"}
        self.failing = failing or set()
        self.fail_times = dict(fail_times or {})
        self.build_calls: list[str] = []
        self._version = "v1"

    @property
    def source_version(self) -> str:
        return self._version

    def has_model(self, model_id: str) -> bool:
        return model_id in self.known

    def build_stl(self, model_id: str, destination: Path) -> ModelResult:
        self.build_calls.append(model_id)
        if model_id in self.failing:
            raise ProviderError(f"model {model_id} is unavailable")
        remaining = self.fail_times.get(model_id, 0)
        if remaining > 0:
            self.fail_times[model_id] = remaining - 1
            raise ProviderError(f"temporary failure for {model_id}")
        write_cube_stl(destination)
        return ModelResult(model_id=model_id, path=destination,
                           size_bytes=destination.stat().st_size, triangles=12,
                           dimensions=(10.0, 10.0, 10.0), warnings=[],
                           source_version=self.source_version)


class FakeRegistry:
    """Stands in for ProviderRegistry with the fakes above."""

    def __init__(self, set_provider: FakeSetProvider, model_provider: FakeModelProvider):
        self._set = set_provider
        self._model = model_provider

    @property
    def set_provider(self):
        return self._set

    @property
    def part_provider(self):
        return self._set

    @property
    def fallback_set_provider(self):
        return None

    def available_model_providers(self):
        return [self._model]

    def find_model_provider(self, model_id: str):
        return self._model if self._model.has_model(model_id) else None

    def health(self) -> dict:
        return {"ready": True, "set_provider": self._set.name,
                "part_provider": self._set.name, "sources": {}}


@pytest.fixture
def fake_set_provider() -> FakeSetProvider:
    return FakeSetProvider()


@pytest.fixture
def fake_model_provider() -> FakeModelProvider:
    return FakeModelProvider()


@pytest.fixture
def registry(fake_set_provider, fake_model_provider) -> FakeRegistry:
    return FakeRegistry(fake_set_provider, fake_model_provider)


@pytest.fixture
def worker(settings, db, registry):
    from app.services.cache_service import CacheService
    from app.services.job_service import JobManager
    from app.services.zip_service import ZipService
    from app.workers.generation_worker import GenerationWorker

    cache = CacheService(db, settings.cache_directory)
    zips = ZipService(settings.output_directory, settings.max_zip_bytes)
    jobs = JobManager(db)
    return GenerationWorker(settings, registry, cache, zips, jobs)


# --- real-data helpers ----------------------------------------------------

@pytest.fixture(scope="session")
def real_settings() -> Settings:
    from app.config import get_settings
    return get_settings()


@pytest.fixture(scope="session")
def ldraw_library(real_settings):
    from app.ldraw.parser import LDrawLibrary
    library = LDrawLibrary(real_settings.ldraw_dir)
    if not library.available:
        pytest.skip("LDraw library not installed (run scripts/bootstrap_data.py)")
    return library


def corrupt_stl(path: Path) -> Path:
    """A binary STL whose triangle count does not match its length."""
    with open(path, "wb") as fh:
        fh.write(b"\0" * 80)
        fh.write(struct.pack("<I", 500))       # claims 500 triangles
        fh.write(b"\0" * 50)                   # provides one
    return path

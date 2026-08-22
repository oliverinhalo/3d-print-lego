"""Model provider serving STL files a user has supplied themselves.

Use this to plug in geometry the application cannot fetch for you — for
example your own legally obtained exports, or a commercial parts library
you have licensed.  Drop files named ``<part_id>.stl`` into a directory and
point ``LOCAL_MODEL_DIRECTORY`` at it; matching parts are picked up first.

Files are validated exactly like generated geometry, and are copied (never
symlinked) into the cache so a later change to the source directory cannot
silently alter a previously generated set.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..ldraw.stl import validate_stl_file
from .base import ModelProvider, ModelResult, ProviderError, ProviderUnavailable

SUPPORTED_SUFFIXES = (".stl", ".STL")


class LocalDirectoryModelProvider(ModelProvider):
    name = "local"

    def __init__(self, directory: Path, source_version: str = "local"):
        self.directory = Path(directory)
        self._version = source_version

    @property
    def available(self) -> bool:
        return self.directory.is_dir()

    def health(self) -> dict:
        count = 0
        if self.available:
            count = sum(1 for p in self.directory.iterdir()
                        if p.suffix in SUPPORTED_SUFFIXES)
        return {"name": self.name, "available": self.available,
                "directory": str(self.directory), "models": count}

    @property
    def source_version(self) -> str:
        return self._version

    def _locate(self, model_id: str) -> Path | None:
        # model_id comes from catalogue data, but treat it as untrusted:
        # never let it escape the configured directory.
        if not model_id or "/" in model_id or "\\" in model_id or ".." in model_id:
            return None
        for suffix in SUPPORTED_SUFFIXES:
            candidate = self.directory / f"{model_id}{suffix}"
            try:
                resolved = candidate.resolve()
                resolved.relative_to(self.directory.resolve())
            except (OSError, ValueError):
                continue
            if resolved.is_file():
                return resolved
        return None

    def has_model(self, model_id: str) -> bool:
        return self.available and self._locate(model_id) is not None

    def build_stl(self, model_id: str, destination: Path) -> ModelResult:
        if not self.available:
            raise ProviderUnavailable(f"{self.directory} does not exist")
        source = self._locate(model_id)
        if source is None:
            raise ProviderError(f"no local STL for {model_id}")

        validation = validate_stl_file(source)
        if not validation.ok:
            raise ProviderError(
                f"supplied STL failed validation: {'; '.join(validation.errors)}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return ModelResult(
            model_id=model_id,
            path=destination,
            size_bytes=destination.stat().st_size,
            triangles=validation.triangles,
            dimensions=validation.dimensions,
            warnings=validation.warnings,
            source_version=self._version,
        )

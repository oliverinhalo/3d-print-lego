"""Model provider backed by the LDraw Parts Library.

The LDraw Parts Library is a community-maintained, openly licensed
(CC BY 2.0 / CC BY 4.0) collection of ~24,000 LEGO element definitions,
distributed as a single downloadable archive from
https://library.ldraw.org/updates .  There is no API to abuse and no
authentication to work around: the archive is meant to be downloaded and
used, which makes it the right primary geometry source for this project.

Part ids line up with LEGO design ids, which is also what Rebrickable uses
for the majority of parts, so ``3001`` -> ``parts/3001.dat`` resolves
directly for most of a set's inventory.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from ..ldraw.convert import convert_part
from ..ldraw.parser import LDrawError, LDrawLibrary
from .base import ModelProvider, ModelResult, ProviderError, ProviderUnavailable

#: Bump when the conversion output changes; invalidates every cached STL.
CONVERTER_VERSION = "1"


class LDrawModelProvider(ModelProvider):
    name = "ldraw"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.library = LDrawLibrary(settings.ldraw_dir)
        self._version: str | None = None

    # --- availability -----------------------------------------------------
    @property
    def available(self) -> bool:
        return self.library.available

    def health(self) -> dict:
        return {
            "name": self.name,
            "available": self.available,
            "parts": self.library.part_count() if self.available else 0,
            "version": self.source_version,
            "root": str(self.library.root),
        }

    def _require(self) -> None:
        if not self.available:
            raise ProviderUnavailable(
                "The LDraw parts library is not installed. "
                "Run: python scripts/bootstrap_data.py")

    # --- versioning -------------------------------------------------------
    @property
    def source_version(self) -> str:
        """Library release, read from the bootstrap marker or the archive."""
        if self._version is None:
            marker = self.settings.source_directory / "ldraw_version.json"
            version = "unknown"
            if marker.exists():
                try:
                    version = json.loads(marker.read_text()).get("version", "unknown")
                except (OSError, ValueError):
                    version = "unknown"
            self._version = version
        return self._version

    @property
    def converter_version(self) -> str:
        # Geometry settings change the bytes we emit, so they belong in the key.
        s = self.settings
        return (f"{CONVERTER_VERSION}-ldu{s.ldu_mm}-scale{s.part_scale}"
                f"-{s.orient_strategy if s.auto_orient else 'raw'}"
                f"-{'bin' if s.stl_binary else 'ascii'}")

    # --- model access -----------------------------------------------------
    def has_model(self, model_id: str) -> bool:
        if not self.available:
            return False
        return self.library.has_part(model_id)

    def describe(self, model_id: str) -> str | None:
        return self.library.part_title(model_id)

    def build_stl(self, model_id: str, destination: Path) -> ModelResult:
        self._require()
        s = self.settings
        try:
            result = convert_part(
                self.library, model_id, destination,
                ldu_mm=s.ldu_mm, scale=s.part_scale,
                auto_orient=s.auto_orient, orient_strategy=s.orient_strategy,
                binary=s.stl_binary,
            )
        except LDrawError as exc:
            raise ProviderError(str(exc)) from exc

        if not result.ok:
            raise ProviderError(
                f"generated geometry failed validation: {'; '.join(result.validation.errors)}")

        return ModelResult(
            model_id=model_id,
            path=destination,
            size_bytes=result.size_bytes,
            triangles=result.validation.triangles,
            dimensions=result.validation.dimensions,
            warnings=result.validation.warnings,
            source_version=self.source_version,
        )

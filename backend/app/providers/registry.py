"""Provider selection.

Everything downstream depends on this module rather than on a concrete
source, so changing ``SET_PROVIDER`` / ``MODEL_PROVIDER`` (or adding a new
class) is all that is needed to swap a data source.

Model providers are consulted as an ordered chain: the first provider that
actually has a given shape wins.  That makes "use the best available source
automatically" the default behaviour, and lets a user's own local models
take precedence over generated ones.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..config import Settings
from ..db import Database
from .base import ModelProvider, PartProvider, ProviderError, SetProvider
from .ldraw_model import LDrawModelProvider
from .local_directory import LocalDirectoryModelProvider
from .mecabricks import MecabricksModelProvider
from .rebrickable_api import RebrickableAPIProvider
from .rebrickable_csv import RebrickableCSVProvider


class ProviderRegistry:
    """Builds and holds the providers for one application instance."""

    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

        self.csv_provider = RebrickableCSVProvider(db)
        self.api_provider = RebrickableAPIProvider(settings.rebrickable_api_key)

        self._set_provider: SetProvider
        self._part_provider: PartProvider
        if settings.set_provider == "rebrickable_api" and self.api_provider.available:
            self._set_provider = self.api_provider
            self._part_provider = self.api_provider
        else:
            self._set_provider = self.csv_provider
            self._part_provider = self.csv_provider

        self.model_providers: list[ModelProvider] = []
        local_dir = os.environ.get("LOCAL_MODEL_DIRECTORY", "").strip()
        if local_dir:
            self.model_providers.append(LocalDirectoryModelProvider(Path(local_dir)))
        self.ldraw_provider = LDrawModelProvider(settings)
        self.model_providers.append(self.ldraw_provider)
        # Present in health output so its unavailability is visible, not silent.
        self.mecabricks_provider = MecabricksModelProvider()

    # --- accessors --------------------------------------------------------
    @property
    def set_provider(self) -> SetProvider:
        return self._set_provider

    @property
    def part_provider(self) -> PartProvider:
        return self._part_provider

    @property
    def fallback_set_provider(self) -> SetProvider | None:
        """Used when the primary provider does not know a set."""
        if self._set_provider is self.csv_provider and self.api_provider.available:
            return self.api_provider
        return None

    def available_model_providers(self) -> list[ModelProvider]:
        return [p for p in self.model_providers if getattr(p, "available", True)]

    def find_model_provider(self, model_id: str) -> ModelProvider | None:
        """First provider in the chain that actually has this shape."""
        for provider in self.available_model_providers():
            try:
                if provider.has_model(model_id):
                    return provider
            except ProviderError:
                continue
        return None

    # --- diagnostics ------------------------------------------------------
    def health(self) -> dict:
        return {
            "set_provider": self._set_provider.name,
            "part_provider": self._part_provider.name,
            "sources": {
                "rebrickable_csv": self.csv_provider.health(),
                "rebrickable_api": self.api_provider.health(),
                "ldraw": self.ldraw_provider.health(),
                "mecabricks": self.mecabricks_provider.health(),
                **({"local": self.model_providers[0].health()}
                   if isinstance(self.model_providers[0], LocalDirectoryModelProvider) else {}),
            },
            "ready": self.csv_provider.available and self.ldraw_provider.available,
        }

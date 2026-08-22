"""Mecabricks model provider — deliberately NOT implemented.

Findings from investigating Mecabricks as an automated STL source
(checked 2026-08-22):

* There is no public or documented API.  Every ``/api/...`` path returns a
  zero-length ``text/html`` response (the site's SPA catch-all), not JSON.
* No published endpoint, dataset download, or supported export mechanism
  for programmatic access exists.
* ``robots.txt`` disallows ``/workshop/`` (the editor that performs STL
  export) as well as the library search paths.
* Export happens client-side inside an authenticated browser workshop
  session and is governed by an export agreement the user accepts
  individually.

Automating that would mean driving an authenticated browser UI against the
site's wishes, so this provider intentionally raises instead.  The class is
kept so a legitimate route (an official API, a licensed dataset, or a user
supplying their own exported models) can be dropped in later without any
change to the pipeline.

If you have your own legally obtained Mecabricks exports, point
``LocalDirectoryModelProvider`` at them instead — see docs/PROVIDERS.md.
"""
from __future__ import annotations

from pathlib import Path

from .base import ModelProvider, ModelResult, ProviderUnavailable

UNAVAILABLE_REASON = (
    "Mecabricks does not offer a public API or a supported automated export "
    "mechanism, and its robots.txt disallows the workshop paths where export "
    "happens. This provider is therefore not implemented; the LDraw provider "
    "is used instead."
)


class MecabricksModelProvider(ModelProvider):
    name = "mecabricks"

    @property
    def available(self) -> bool:
        return False

    def health(self) -> dict:
        return {"name": self.name, "available": False, "reason": UNAVAILABLE_REASON}

    @property
    def source_version(self) -> str:
        return "unavailable"

    def has_model(self, model_id: str) -> bool:
        return False

    def build_stl(self, model_id: str, destination: Path) -> ModelResult:
        raise ProviderUnavailable(UNAVAILABLE_REASON)

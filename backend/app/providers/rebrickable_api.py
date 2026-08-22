"""Optional live Rebrickable API provider.

The CSV dataset is the default because it needs no key and no network at
request time.  This provider exists for one real case: a set released after
your dataset snapshot.  It requires a free API key from
https://rebrickable.com/api/ and respects the documented limit of roughly
one request per second.

Only fixed, documented endpoints on ``rebrickable.com`` are called; user
input is never used to build an arbitrary URL.
"""
from __future__ import annotations

import threading
import time

import httpx

from ..models.set import InventoryEntry, LegoSet, SetInventory
from .base import PartProvider, ProviderError, ProviderUnavailable, SetNotFound, SetProvider

BASE_URL = "https://rebrickable.com/api/v3/lego"
MIN_INTERVAL = 1.05          # seconds between requests (documented ~1/sec)
PAGE_SIZE = 500
MAX_PAGES = 40


class _Throttle:
    """Process-wide minimum spacing between outbound requests."""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.interval:
                time.sleep(self.interval - delta)
            self._last = time.monotonic()


class RebrickableAPIProvider(SetProvider, PartProvider):
    name = "rebrickable_api"

    def __init__(self, api_key: str, timeout: float = 30.0):
        self.api_key = (api_key or "").strip()
        self._throttle = _Throttle(MIN_INTERVAL)
        self._client: httpx.Client | None = None
        self._timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def health(self) -> dict:
        return {"name": self.name, "available": self.available,
                "reason": None if self.available else "REBRICKABLE_API_KEY is not set"}

    def _require(self) -> None:
        if not self.available:
            raise ProviderUnavailable(
                "REBRICKABLE_API_KEY is not set; the live API provider is unavailable.")

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                headers={
                    "Authorization": f"key {self.api_key}",
                    "Accept": "application/json",
                    "User-Agent": "lego-stl-generator/1.0 (+https://github.com/)",
                },
                # connection pooling; keeps us polite and fast
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
                follow_redirects=False,
            )
        return self._client

    def _get(self, path: str, params: dict | None = None, attempts: int = 4) -> dict:
        """GET a fixed API path with throttling, retries and backoff."""
        self._require()
        url = f"{BASE_URL}/{path.lstrip('/')}"
        delay = 1.0
        last_error: Exception | None = None
        for attempt in range(attempts):
            self._throttle.wait()
            try:
                response = self.client.get(url, params=params)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code == 404:
                    raise SetNotFound("Not found in the Rebrickable API.")
                if response.status_code == 401:
                    raise ProviderUnavailable("Rebrickable rejected the API key.")
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = ProviderError(
                        f"Rebrickable returned HTTP {response.status_code}")
                else:
                    response.raise_for_status()
                    return response.json()
            if attempt < attempts - 1:
                time.sleep(delay)
                delay *= 2      # exponential backoff
        raise ProviderError(f"Rebrickable API request failed: {last_error}")

    # --- SetProvider ------------------------------------------------------
    def get_set(self, set_num: str) -> LegoSet:
        data = self._get(f"sets/{set_num}/")
        return LegoSet(
            set_num=data["set_num"],
            name=data.get("name", set_num),
            year=data.get("year"),
            theme=None,
            num_parts=data.get("num_parts", 0) or 0,
            img_url=data.get("set_img_url"),
        )

    def search(self, text: str, limit: int = 10) -> list[LegoSet]:
        data = self._get("sets/", {"search": text, "page_size": min(limit, 100)})
        return [
            LegoSet(set_num=r["set_num"], name=r.get("name", ""), year=r.get("year"),
                    num_parts=r.get("num_parts", 0) or 0, img_url=r.get("set_img_url"))
            for r in data.get("results", [])
        ]

    # --- PartProvider -----------------------------------------------------
    def get_inventory(self, lego_set: LegoSet, include_spares: bool = False) -> SetInventory:
        entries: list[InventoryEntry] = []
        page = 1
        while page <= MAX_PAGES:
            data = self._get(f"sets/{lego_set.set_num}/parts/",
                             {"page": page, "page_size": PAGE_SIZE})
            for row in data.get("results", []):
                if row.get("is_spare") and not include_spares:
                    continue
                part = row.get("part", {})
                colour = row.get("color", {})
                entries.append(InventoryEntry(
                    part_num=part.get("part_num", ""),
                    name=part.get("name", part.get("part_num", "")),
                    quantity=int(row.get("quantity", 0) or 0),
                    color_id=colour.get("id"),
                    color_name=colour.get("name"),
                    is_spare=bool(row.get("is_spare")),
                    img_url=part.get("part_img_url"),
                ))
            if not data.get("next"):
                break
            page += 1
        return SetInventory(lego_set=lego_set, entries=[e for e in entries if e.quantity > 0])

    def geometry_candidates(self, part_num: str) -> list[tuple[str, str]]:
        """Use the API's ``external_ids`` to find the LDraw id for a part."""
        candidates: list[tuple[str, str]] = [(part_num, "direct")]
        try:
            data = self._get(f"parts/{part_num}/")
        except ProviderError:
            return candidates
        for ldraw_id in (data.get("external_ids", {}) or {}).get("LDraw", []) or []:
            if ldraw_id and ldraw_id != part_num:
                candidates.append((str(ldraw_id), "ldraw_external_id"))
        parent = data.get("print_of")
        if parent:
            candidates.append((str(parent), "print_parent"))
        return candidates

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

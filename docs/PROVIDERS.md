# Data source investigation

What was checked, what was found, and why each source was chosen or
rejected. All findings verified **2026-08-22**.

## Summary

| Source | API? | Automatable? | Used for |
|---|---|---|---|
| Rebrickable CSV datasets | Bulk download, no auth | **Yes** | Sets + inventories (default) |
| Rebrickable REST API | Yes, free key | **Yes**, ~1 req/s | Sets newer than your snapshot (optional) |
| LDraw Parts Library | Bulk download, no auth | **Yes** | Geometry (default) |
| Mecabricks | **No** | **No** | — (documented stub) |
| BrickOwl | Key on request | Not needed | — |
| Brickset | Key on request | Not needed | — |

---

## Rebrickable — chosen for set and part data

**CSV datasets** (`https://cdn.rebrickable.com/media/downloads/*.csv.gz`) are
published for bulk download with no key and no account. Roughly 16 MB
gzipped, imported into SQLite in about 8 seconds:

| File | Rows |
|---|---:|
| `sets.csv` | 28 116 |
| `parts.csv` | 64 297 |
| `inventories.csv` | 47 197 |
| `inventory_parts.csv` | 1 550 853 |
| `part_relationships.csv` | 37 210 |
| `themes.csv` / `colors.csv` | 496 / 275 |

This is the default because it needs no credentials, makes lookups local and
instant, and removes any per-request load on someone else's servers.

**The REST API** (`/api/v3/lego/...`) exists, is documented, and requires a
free key. Its rate policy is ~1 request/second with 429s and possible IP
bans for ignoring them — so `RebrickableAPIProvider` throttles to one request
per 1.05 s, pools connections, and backs off exponentially. It is optional
and used only for sets newer than your local snapshot.

### Why `part_relationships` matters

A set inventory lists *decorated, coloured* parts. A printer needs *shapes*.
`part_relationships.csv` provides the mapping:

```
rel_type  child            parent
P         3070bpr0056  →   3070b     (printed version of)
T         116173pat0001 →  116173    (patterned version of)
M         …                          (mould variant)
A         …                          (alternate)
```

Resolving `P`/`T` chains, then falling back to suffix stripping
(`3070bpr0056` → `3070b`) and mould variants, lifts LDraw coverage on real
sets from **~82% to 96–100% of pieces**.

---

## LDraw — chosen for geometry

`https://library.ldraw.org/library/updates/complete.zip` — a single ~145 MB
archive, no authentication, explicitly published for download, licensed
**CC BY 2.0 / CC BY 4.0**. Contains 24 591 part files plus 1 780 primitives
and 9 179 sub-parts.

Part ids align with LEGO design ids, which is also what Rebrickable uses for
most parts, so `3001` → `parts/3001.dat` resolves directly for the majority
of an inventory.

The format is documented and stable, so parsing it is not "scraping
undocumented internals" — it is reading a published file format. Measured
conversion cost is ~35 ms per part, and a sample of 600 random parts
converted with **zero failures**.

---

## Mecabricks — investigated, cannot be automated

The manual workflow this project replaces used Mecabricks, so it was the
first candidate. It was rejected on evidence, not assumption:

```
$ curl -sSI https://www.mecabricks.com/api/v1/parts
HTTP/2 200
content-type: text/html; charset=UTF-8
# 0 bytes of body — the SPA catch-all, not an API
```

* No public or documented API; no endpoint returns JSON.
* No published dataset download or supported export endpoint.
* `robots.txt` disallows `/workshop/` — where export happens — plus
  `/library/search` and other library paths.
* Export runs client-side in an authenticated browser session, governed by
  an export agreement each user accepts individually.

Automating it would require driving an authenticated browser UI against the
site's stated wishes. `MecabricksModelProvider` therefore raises
`ProviderUnavailable` with this explanation. It is kept so a legitimate route
— an official API, a licensed dataset, or a user's own exports — can be added
later without touching the pipeline.

**If you have your own Mecabricks exports**, set `LOCAL_MODEL_DIRECTORY` to a
folder of `<part_id>.stl` files. `LocalDirectoryModelProvider` is consulted
before LDraw, validates each file, and copies it into the cache.

---

## BrickOwl and Brickset — not needed

Both offer APIs on request. Neither was adopted: Rebrickable's public dataset
already covers set and inventory data completely and without credentials, and
neither provides 3D geometry. The provider interface makes either a drop-in
addition if a catalogue gap ever appears.

---

## Writing a new provider

Implement the relevant interface from `backend/app/providers/base.py` and
register it in `registry.py`.

```python
class MyModelProvider(ModelProvider):
    name = "mysource"

    @property
    def source_version(self) -> str:
        return "2026-08"          # part of the cache key

    def has_model(self, model_id: str) -> bool:
        ...

    def build_stl(self, model_id: str, destination: Path) -> ModelResult:
        ...                       # must write a validated STL in millimetres
```

Model providers are consulted as an ordered chain — the first one that has a
given shape wins — so a new source can supplement LDraw rather than replace
it. Include anything that changes your output bytes in `source_version` or
`converter_version` so the cache invalidates correctly.

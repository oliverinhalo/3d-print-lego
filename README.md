# Brick Foundry — LEGO set → printable STL generator

Enter a LEGO set number. Get back a ZIP containing an STL file for every
physical piece in that set, ready to drag into a slicer and print.

```
#77263  →  find set  →  load inventory  →  match geometry  →  convert  →  ZIP
```

The user never sees a part id, a mesh, an API or a cache. They type a number
and press one button.

---

## 1. What it actually does

Given `77263`, `#77263` or `LEGO 77263`, the application:

1. resolves the number to a catalogue set (`77263-1`, *BMW M3 (E30)*);
2. loads the complete parts inventory (353 pieces, 113 unique parts);
3. collapses colour variants and decorated prints onto **shared geometry**
   (113 parts → 95 distinct shapes to actually convert);
4. converts each distinct shape from LDraw to a millimetre-accurate STL,
   concurrently, caching every result permanently;
5. writes one STL file per physical piece (343 files);
6. packages everything into `LEGO_77263_Print_Pack.zip`.

Measured on a laptop-class machine:

| Set | Pieces | Unique | Shapes converted | STL files | ZIP | Cold | Warm |
|-----|-------:|-------:|-----------------:|----------:|----:|-----:|-----:|
| 77263 BMW M3 | 353 | 113 | 95 | 343 | 5.8 MB | 1.3 s | 0.3 s |
| 31120 Medieval Castle | 1 407 | 211 | 211 | 1 407 | 32.9 MB | 2.1 s | — |
| 21318 Tree House | 3 017 | 256 | 245 | 3 017 | 107 MB | 4.9 s | 0.9 s |

The cache is shared across sets and users, so the second set you generate is
substantially faster than the first.

---

## 2. Installation

Requirements: **Python 3.10+** and **Node 18+** (Node only to build the UI).

```bash
git clone <this repository>
cd 3d-print-lego

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                 # optional: every default already works

python scripts/bootstrap_data.py     # one-time, downloads ~150 MB

npm --prefix frontend install
npm --prefix frontend run build

python run.py                        # → http://127.0.0.1:8000
```

`bootstrap_data.py` downloads the two data sources described below and
imports the catalogue into SQLite (about 1.7 million rows, ~8 seconds). It
is safe to re-run; `--skip-existing` keeps what you already have.

### Development

Two processes, with hot reload on both:

```bash
python run.py --reload                  # backend on :8000
npm --prefix frontend run dev           # frontend on :5173, proxies /api
```

Or use the helper that runs both:

```bash
./scripts/dev.sh
```

### Tests

```bash
pip install -r requirements-dev.txt
pytest                    # 174 tests
pytest -m "not realdata"  # skip tests needing the downloaded data
```

---

## 3. Configuration

Everything is environment-driven; see `.env.example` for the annotated list.
No secrets are hard-coded, and **no API key is required** for the default
configuration.

The settings worth knowing about:

| Variable | Default | Meaning |
|---|---|---|
| `MAX_CONCURRENT_DOWNLOADS` | `8` | Parallel geometry conversions |
| `MAX_PIECES_PER_JOB` | `6000` | Refuses absurdly large jobs |
| `JOB_RETENTION` | `86400` | Seconds before a finished ZIP is deleted |
| `CACHE_RETENTION` | `2592000` | Seconds before unused geometry is dropped |
| `PART_SCALE` | `1.0` | Uniform scale applied to every part (see §8) |
| `ORIENT_STRATEGY` | `native` | `native` keeps studs up; `flat` lays parts down |
| `SET_PROVIDER` | `rebrickable_csv` | `rebrickable_csv` or `rebrickable_api` |
| `REBRICKABLE_API_KEY` | *(empty)* | Only for `rebrickable_api` |
| `LOCAL_MODEL_DIRECTORY` | *(unset)* | Your own STLs, tried before LDraw |

---

## 4. External services and providers

Everything is behind a provider interface (`backend/app/providers/`), so a
source can be replaced without touching the pipeline. See
[docs/PROVIDERS.md](docs/PROVIDERS.md) for the full findings.

| Role | Implementation | Source | Key needed |
|---|---|---|---|
| `SetProvider` / `PartProvider` | `RebrickableCSVProvider` **(default)** | [Rebrickable CSV downloads](https://rebrickable.com/downloads/) | **No** |
| `SetProvider` / `PartProvider` | `RebrickableAPIProvider` | [Rebrickable API](https://rebrickable.com/api/) | Yes (free) |
| `ModelProvider` | `LDrawModelProvider` **(default)** | [LDraw Parts Library](https://library.ldraw.org/) | **No** |
| `ModelProvider` | `LocalDirectoryModelProvider` | Your own STL files | No |
| `ModelProvider` | `MecabricksModelProvider` | — | **Not implemented, see below** |

### Why not Mecabricks?

The original manual workflow was *LEGO part → Mecabricks → export STL*, so
Mecabricks was investigated first. It cannot be automated legitimately:

* **There is no public or documented API.** Every `/api/...` path returns a
  zero-length `text/html` response — the site's single-page-app catch-all —
  not JSON. (Verified 2026-08-22.)
* **No dataset download or supported export endpoint** is published.
* **`robots.txt` disallows `/workshop/`**, which is where export happens, as
  well as the library search paths.
* Export runs client-side inside an authenticated browser session, under an
  export agreement each user accepts individually.

Automating it would mean driving an authenticated browser UI against the
site's stated wishes. So `MecabricksModelProvider` deliberately raises with
an explanation instead of pretending to work. If Mecabricks ever publishes
an API, it drops into the existing interface with no pipeline changes.

**LDraw is used instead**, and is arguably the better source anyway: it is an
openly licensed library explicitly published for download, covering ~24 600
parts, with part ids that align with LEGO design ids.

### How much of a set can actually be printed?

Measured across real sets: **96–100% of pieces**. The gaps are:

* *very new elements* not yet modelled in LDraw (a 2026 set will have a few);
* *stickers, instructions and cloth*, which are excluded deliberately —
  they are not printable objects.

Anything unmatched is reported per-part in the UI, never silently dropped.

---

## 5. How model caching works

```
data/cache/<provider>/<shard>/<model_id>.stl
```

A shape is converted **at most once, ever**. Requests for the same shape —
from other colours in the same set, from later jobs, from other users — reuse
the cached STL. Because LEGO sets reuse elements heavily, this is the single
biggest performance lever in the system: 40 Technic pins cost one conversion
and 40 file copies.

Metadata lives in SQLite (`geometry_cache`): part id, provider, source model
version, converter version, SHA-256, size, triangle count, dimensions, and
timestamps. An entry is invalidated automatically when:

* the upstream library version changes (a new LDraw release), or
* `converter_version` changes — which includes the geometry settings, so
  editing `PART_SCALE` or `ORIENT_STRATEGY` correctly rebuilds affected STLs.

Unused entries are purged after `CACHE_RETENTION`.

---

## 6. How STL conversion works

```
LDraw .dat  →  parse  →  normalise units  →  orient  →  validate  →  STL
```

**Parse.** LDraw files are a recursive scene description: line type `1`
references a sub-file with a 3×3 matrix and translation, `3`/`4` are triangles
and quads, and `0 BFC` meta-commands declare face winding. Correct output
requires tracking winding through `CW`/`CCW`, `INVERTNEXT`, and the sign of
the accumulated transform determinant (a mirrored matrix flips handedness).
Sub-files are parsed once into their own coordinate space and cached, which
turns a potentially exponential walk into a near-linear one — about 35 ms per
part.

**Units.** LDraw uses LDU with Y pointing down; slicers use millimetres with
Z up. The pipeline applies `(x, y, z) → (x, z, −y)` and scales by
`LDU_MM = 0.4`. This puts a 2×4 brick at exactly **32.00 × 16.00 × 11.20 mm**,
which the test suite asserts against seven known elements. STL itself encodes
no units, so this normalisation is the only thing standing between you and a
model 1000× too small — the validator checks for exactly that.

**Orient.** Parts are rested on Z=0 and centred, keeping LDraw's own
orientation, which already has the flat underside down and studs up. That is
the sensible printable face for nearly every LEGO element. (Minimising height
instead would tip a 1×1 brick onto its side and put its stud on an overhang,
so the default deliberately does not do that.) Final layout is the slicer's
job via Auto Arrange.

**Validate.** Every generated STL is checked for: file existence, plausible
size, valid binary structure, triangle count > 0, NaN/infinite coordinates,
degenerate triangles, plausible dimensions (0.5–800 mm), and mesh closure.

### A note on watertightness

LDraw parts are *surface* models. Where analytically generated primitives
meet — a stud base against a plate top, a cylinder against its cap — the
edges frequently do not stitch. Typically about **4% of edges are open**
seams, not holes. Bambu Studio, OrcaSlicer, Cura and PrusaSlicer all repair
this automatically on import, which is why it is reported as a warning rather
than an error. Set `require_manifold` if you want it treated as fatal.

---

## 7. Troubleshooting failed parts

A failing part never fails the job. The pipeline retries with exponential
backoff, then records the failure against that part and continues; the ZIP is
built from everything that worked and the job finishes as `partial`.

The UI shows part id, name, quantity and reason, with **Retry failed parts**
(which re-runs only those parts and rebuilds the ZIP, keeping successful
work) and **Download available parts**.

| Message | Meaning | What to do |
|---|---|---|
| `No 3D model is available for this part.` | Not in LDraw — usually a very new element | Wait for an LDraw update, or supply your own STL via `LOCAL_MODEL_DIRECTORY` |
| `generated geometry failed validation` | Conversion produced an implausible mesh | Report it; check `PART_SCALE` is sane |
| `The LDraw parts library is not installed.` | Bootstrap not run | `python scripts/bootstrap_data.py` |
| `LEGO set … was not found` | Not in your catalogue snapshot | Re-run bootstrap, or set `REBRICKABLE_API_KEY` and `SET_PROVIDER=rebrickable_api` |

Check `GET /api/health` for provider status, dataset counts and cache size.

---

## 8. Printing notes

**Dimensions are nominal.** LDraw models a 2×4 brick as exactly 32.00 mm;
a moulded LEGO brick is 31.80 mm, deliberately undersized to leave clutch
tolerance. Printed parts may therefore be tight against genuine bricks. Set
`PART_SCALE=0.994` to reproduce real-brick dimensions. The default is `1.0`
because the spec's rule is to preserve source geometry unless asked.

Clutch power depends far more on your printer's tolerances than on this
setting — expect to calibrate. Small elements need a fine nozzle (0.2 mm) and
a low layer height.

---

## 9. Architecture

```
backend/app/
    api/          routes.py, deps.py       HTTP + SSE, rate limiting
    providers/    base.py                  SetProvider/PartProvider/ModelProvider
                  rebrickable_csv.py       catalogue from public CSV dumps
                  rebrickable_api.py       optional live API (throttled)
                  ldraw_model.py           geometry from the LDraw library
                  local_directory.py       your own STL files
                  mecabricks.py            documented, non-functional stub
                  registry.py              provider selection + chaining
    services/     cache_service.py         persistent geometry cache
                  zip_service.py           streaming ZIP assembly
                  job_service.py           job registry + SSE event bus
                  naming_service.py        cross-platform filenames
                  normalize.py             input validation
    workers/      generation_worker.py     the pipeline
    ldraw/        parser.py, mesh.py, stl.py, convert.py
    models/       set.py, part.py, job.py
frontend/src/     React + TypeScript (Vite)
tests/            174 tests
scripts/          bootstrap_data.py, dev.sh
```

Generation is job-based: `POST /api/generate` returns a job id immediately
and the HTTP request never blocks on the work. Progress streams over SSE,
with polling as an automatic fallback.

### API

| Endpoint | Purpose |
|---|---|
| `POST /api/generate` | Start a job → `{ job_id, set_num }` |
| `GET /api/jobs/{id}/events` | SSE progress stream |
| `GET /api/jobs/{id}` | Job summary (polling fallback) |
| `GET /api/jobs/{id}/parts` | Full part list with statuses |
| `POST /api/jobs/{id}/retry` | Retry only the failed parts |
| `GET /api/jobs/{id}/download` | The ZIP |
| `GET /api/sets/{number}` | Set preview without generating |
| `GET /api/health` | Providers, cache and job diagnostics |

Event types: `job_started`, `set_found`, `inventory_loaded`,
`parts_identified`, `models_resolved`, `part_progress`, `zip_progress`,
`job_complete`, `job_failed`.

---

## 10. Production

Runs as a single process on a normal home server or VPS — no Kubernetes, no
message broker, no external database. `python run.py` serves the API *and*
the built frontend on one port.

```bash
python run.py --host 0.0.0.0 --port 8000
```

Put nginx or Caddy in front for TLS. For SSE, disable proxy buffering
(`proxy_buffering off;`); the app already sends `X-Accel-Buffering: no`.

Docker is supported but optional:

```bash
docker compose up --build
```

Keep `--workers 1` unless you add a shared job store: jobs live in the
process that created them.

**Security.** Input is validated and normalised; filenames are sanitised for
Windows/macOS/Linux; job ids and cache keys are checked against their root
directories to prevent traversal; downloads are served only from recorded job
paths; job size and ZIP size are capped; generation is rate-limited per IP;
abandoned jobs and stale cache entries are cleaned up on a timer. **No
endpoint accepts a URL for the server to fetch**, so there is no SSRF surface.

---

## 11. Legal and licensing

* **LDraw Parts Library** — CC BY 2.0 / CC BY 4.0. Freely redistributable
  with attribution; the generated ZIP credits it in `README.txt`.
* **Rebrickable data** — downloaded from their public datasets page, used
  under their terms. The optional API provider respects the documented
  ~1 request/second limit, with backoff.
* **No scraping, and no circumvention.** The application does not bypass
  authentication, CAPTCHAs, rate limits, access controls or anti-bot
  measures, and does not depend on undocumented website internals. Where a
  source could not be automated legitimately (Mecabricks), it is left
  unimplemented and documented rather than forced.
* **LEGO® is a trademark of the LEGO Group**, which does not sponsor,
  authorise or endorse this project. Printed parts are for personal use;
  LEGO element designs may be subject to design rights and trademarks in your
  jurisdiction. Selling printed copies is your responsibility to assess.

This project's own code is MIT licensed. That covers the code only — not the
part geometry, which remains under LDraw's CC BY terms.

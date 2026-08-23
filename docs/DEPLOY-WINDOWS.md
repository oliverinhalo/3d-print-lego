# Running on Windows Server with Docker

The whole install is **one file and one command**. The container downloads the
LEGO catalogue and the LDraw parts library by itself on first start, so there
is nothing to run afterwards.

## Requirements

* **Docker Desktop for Windows** (or Docker Engine) set to **Linux containers**
  — the WSL2 backend. The image is Linux-based; Windows-container mode will
  not build it.
* That is all. No Python, no Node, no database on the host.

## Install

```powershell
git clone https://github.com/oliverinhalo/3d-print-lego.git
cd 3d-print-lego
git checkout claude/lego-stl-generator-89vgpt
```

Open `docker-compose.yml` and set your port on this line:

```yaml
    ports:
      - "8080:8000"
```

Change **only the left number** — that is the port on your server. The right
side is inside the container and must stay `8000`. So for port 9500:

```yaml
      - "9500:8000"
```

Then:

```powershell
docker compose up -d
```

That is the entire install.

## First start takes a few minutes

The container downloads ~150 MB (the catalogue plus 24,591 LDraw parts) and
imports 1.7 million catalogue rows before it starts serving. Watch it:

```powershell
docker compose logs -f
```

You will see:

```
  First run: downloading the LEGO catalogue and parts library.
  This is about 150 MB and happens only once.
== Rebrickable catalogue ==
== LDraw parts library ==
  installed 24591 parts
== Importing catalogue into SQLite ==
  imported 1,728,444 rows in 8.5s
Data ready.
→ http://0.0.0.0:8000
```

Once you see `Application startup complete`, browse to
`http://localhost:8080` (or whichever port you chose) to confirm it works
before putting nginx in front.

Restarts after that take a couple of seconds — the data lives in a Docker
named volume (`brick-foundry-data`) and is not downloaded again.

## Everyday commands

| Task | Command |
|---|---|
| Start | `docker compose up -d` |
| Stop | `docker compose down` |
| Logs | `docker compose logs -f` |
| Restart | `docker compose restart` |
| Update after `git pull` | `docker compose up -d --build` |
| Check health | `curl http://localhost:8080/api/health` |

`docker compose down` keeps your data. To wipe it and re-download from
scratch, use `docker compose down -v`.

## nginx

Point your `lego.jacoblevy.co.uk` server block at the port you chose.

Two settings genuinely matter here, so do not omit them:

* **`proxy_buffering off`** — progress is streamed with Server-Sent Events.
  With buffering on, nginx holds the stream and the page appears frozen on
  "Finding LEGO set" until generation finishes.
* **A large `client_max_body_size` is not needed**, but long timeouts are:
  a big set takes several seconds and the ZIP can be 100 MB+.

```nginx
server {
    listen 443 ssl;
    server_name lego.jacoblevy.co.uk;

    # ssl_certificate ... (certbot / your existing TLS config)

    location / {
        proxy_pass http://127.0.0.1:8080;   # ← the port you chose

        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Required for the live progress stream (SSE).
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

The app already sends `X-Accel-Buffering: no`, so nginx will not buffer the
event stream even if you forget — but set `proxy_buffering off` anyway, since
it also keeps large ZIP downloads from being spooled to disk first.

Once nginx is working, consider binding the container to localhost so the raw
port is not reachable from the internet. In `docker-compose.yml`:

```yaml
      - "127.0.0.1:8080:8000"
```

## Tuning

Everything is set in the `environment:` block of `docker-compose.yml`:

| Variable | Default | What it does |
|---|---|---|
| `MAX_CONCURRENT_DOWNLOADS` | `8` | Parallel model conversions. Raise on a big server. |
| `MAX_PIECES_PER_JOB` | `6000` | Rejects absurdly large sets. |
| `JOB_RETENTION` | `86400` | Seconds before finished ZIPs are deleted. |
| `CACHE_RETENTION` | `2592000` | Seconds before unused geometry is dropped. |
| `PART_SCALE` | `1.0` | `0.994` matches real brick dimensions. |
| `AUTO_BOOTSTRAP` | `true` | Auto-download data on first start. |

Apply changes with `docker compose up -d`.

## Disk usage

| What | Size |
|---|---|
| LDraw library + catalogue | ~750 MB |
| Image | ~400 MB |
| STL cache | grows slowly; ~20 MB per few sets, shared across all sets |
| Job ZIPs | deleted after `JOB_RETENTION` |

Budget about 2 GB.

## Troubleshooting

**Container restarts repeatedly on first run.** The health check allows 15
minutes before it reports unhealthy, which is ample for the download. If it
still loops, check `docker compose logs` — usually the server cannot reach
`cdn.rebrickable.com` or `library.ldraw.org` through a corporate proxy or
firewall. The app starts anyway and reports the problem at `/api/health`.

**"no matching manifest for windows/amd64".** Docker is in Windows-container
mode. Right-click the Docker tray icon → *Switch to Linux containers*.

**Page loads but progress never moves.** nginx is buffering the SSE stream —
add `proxy_buffering off;` as above.

**Port already in use.** Change the left-hand number in `docker-compose.yml`
and run `docker compose up -d` again.

# Setup

## Do I need an API key?

**No.** There is nothing to sign up for, no account to create, and no key to
paste anywhere. Every data source is free and public:

* the LEGO set catalogue is a public dataset download,
* the part geometry is the openly licensed LDraw Parts Library.

The app downloads both by itself on first start.

## Do I need to pass any startup arguments?

**No.** Start it and it works. Everything has a working default.

The only setting most people touch is the **port**. Everything else is
optional and listed at the bottom of this page.

## What do I need installed?

Just **Docker**. Not Python, not Node, not a database.

On Windows, install [Docker Desktop](https://www.docker.com/products/docker-desktop/)
and make sure it is running in **Linux container** mode (the default).

---

## Windows: the easy way

1. Download **`install.bat`** from this repository.
2. Put it in a folder of its own, for example `C:\BrickFoundry`.
3. Double-click it.
4. It asks which port you want. Press Enter for **8100**, or type another.

That is the whole install. The script checks Docker, downloads everything,
starts the app and opens it in your browser.

Run `install.bat` again any time to change the port or update to the latest
version. It is safe to run repeatedly.

---

## Anything else: one YAML file

You do **not** need to clone this repository. Docker fetches the source from
GitHub itself.

1. Save `docker-compose.yml` from this repository into an empty folder.
2. In that folder, run:

```bash
docker compose up -d
```

To use a port other than 8100, create a file called `.env` next to it:

```
LEGO_PORT=9000
```

Then open `http://localhost:9000`.

---

## The first start takes a few minutes

Docker builds the image, then the app downloads ~150 MB — the LEGO catalogue
plus 24,591 part models — and imports 1.7 million catalogue rows before it
starts serving. Budget **5–15 minutes** depending on your connection.

Watch it work:

```bash
docker compose logs -f
```

You are looking for:

```
  First run: downloading the LEGO catalogue and parts library.
== LDraw parts library ==
  installed 24591 parts
== Importing catalogue into SQLite ==
  imported 1,728,444 rows
Data ready.
Application startup complete.
```

Every start after that takes about **2 seconds**. The data is kept in a
Docker volume and is never downloaded again.

---

## Everyday commands

Run these from the folder containing `docker-compose.yml`.

| What | Command |
|---|---|
| Start | `docker compose up -d` |
| Stop | `docker compose down` |
| See what it is doing | `docker compose logs -f` |
| Restart | `docker compose restart` |
| Update to the latest version | `docker compose up -d --build` |
| Is it healthy? | `docker compose ps` |

`docker compose down` keeps your data. To delete everything including the
downloaded LEGO data, use `docker compose down -v`.

---

## Optional settings

All optional. Edit the `environment:` block in `docker-compose.yml`, then run
`docker compose up -d` to apply.

| Setting | Default | What it does |
|---|---|---|
| `LEGO_PORT` | `8100` | Port on your machine. Set in `.env`, not here. |
| `PART_SCALE` | `1.0` | `1.0` = exact LDraw size (2x4 brick is 32.00 mm). `0.994` = real LEGO size (31.80 mm), which fits better against genuine bricks. |
| `MAX_CONCURRENT_DOWNLOADS` | `8` | Parts converted at once. Raise on a fast server. |
| `MAX_PIECES_PER_JOB` | `6000` | Rejects sets larger than this. |
| `JOB_RETENTION` | `86400` | Seconds before finished ZIPs are deleted. |
| `CACHE_RETENTION` | `2592000` | Seconds before unused part models are dropped. |
| `AUTO_BOOTSTRAP` | `true` | Download the LEGO data automatically. Leave on. |

---

## Disk space

| What | Size |
|---|---|
| Docker image | ~400 MB |
| LEGO catalogue + parts library | ~750 MB |
| Converted part cache | grows slowly, shared across all sets |
| Finished ZIPs | deleted automatically |

Allow about **2 GB**.

---

## If something goes wrong

**"no matching manifest for windows/amd64"**
Docker is in Windows-container mode. Right-click the Docker tray icon and
choose *Switch to Linux containers*.

**"port is already allocated"**
Something else is using that port. Pick another: run `install.bat` again, or
change `LEGO_PORT` in `.env` and run `docker compose up -d`.

**The container keeps restarting on the very first run**
It is allowed 15 minutes to download before it is considered unhealthy, which
is normally plenty. If it still loops, check `docker compose logs` — usually
the machine cannot reach the download servers because of a firewall or
corporate proxy.

**The page loads but says the catalogue is not installed**
The data download failed. Check `docker compose logs`, fix the network
problem, then `docker compose restart`.

**A few parts fail to generate**
That is expected and not a bug. Very new LEGO elements have not been modelled
in the parts library yet, and stickers and instruction sheets are skipped
because they are not printable. The app tells you exactly which parts, and
still gives you everything else. Typically 96–100% of a set is produced.

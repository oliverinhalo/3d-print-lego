"""SQLite storage: the part index, the geometry cache metadata and job records.

SQLite is used rather than a server database so the application runs on a
home server or VPS with no external services.  The connection is opened in
WAL mode so the background worker can write while requests read.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS sets (
    set_num   TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    year      INTEGER,
    theme_id  INTEGER,
    num_parts INTEGER,
    img_url   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sets_name ON sets(name);

CREATE TABLE IF NOT EXISTS themes (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    parent_id INTEGER
);

CREATE TABLE IF NOT EXISTS parts (
    part_num  TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    part_cat_id INTEGER,
    part_material TEXT
);

CREATE TABLE IF NOT EXISTS colors (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    rgb  TEXT,
    is_trans TEXT
);

CREATE TABLE IF NOT EXISTS inventories (
    id      INTEGER PRIMARY KEY,
    version INTEGER NOT NULL,
    set_num TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inventories_set ON inventories(set_num);

CREATE TABLE IF NOT EXISTS inventory_parts (
    inventory_id INTEGER NOT NULL,
    part_num     TEXT NOT NULL,
    color_id     INTEGER,
    quantity     INTEGER NOT NULL,
    is_spare     INTEGER NOT NULL DEFAULT 0,
    img_url      TEXT
);
CREATE INDEX IF NOT EXISTS idx_inv_parts_inv ON inventory_parts(inventory_id);

CREATE TABLE IF NOT EXISTS part_relationships (
    rel_type        TEXT NOT NULL,
    child_part_num  TEXT NOT NULL,
    parent_part_num TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_child ON part_relationships(child_part_num, rel_type);

-- Persistent geometry cache: one row per converted shape, shared by all jobs.
CREATE TABLE IF NOT EXISTS geometry_cache (
    cache_key        TEXT PRIMARY KEY,   -- "<provider>:<model_id>"
    provider         TEXT NOT NULL,
    model_id         TEXT NOT NULL,
    source_version   TEXT,               -- provider's model/library version
    converter_version TEXT NOT NULL,     -- bumped when conversion changes
    stl_path         TEXT NOT NULL,
    sha256           TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL,
    triangles        INTEGER NOT NULL,
    dim_x            REAL, dim_y REAL, dim_z REAL,
    created_at       REAL NOT NULL,
    last_used_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_used ON geometry_cache(last_used_at);

-- Job records survive a restart so a finished ZIP stays downloadable.
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    query       TEXT,
    set_num     TEXT,
    status      TEXT NOT NULL,
    stage       TEXT,
    payload     TEXT NOT NULL,   -- JSON snapshot of the job summary
    zip_path    TEXT,
    zip_name    TEXT,
    zip_bytes   INTEGER DEFAULT 0,
    created_at  REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    """Thread-local SQLite connections against one database file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        existing = getattr(self._local, "conn", None)
        if existing is None:
            existing = self._new_connection()
            self._local.conn = existing
        return existing

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = self.conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def get_meta(self, key: str) -> str | None:
        row = self.query_one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value))

    def table_count(self, table: str) -> int:
        if not table.isidentifier():
            raise ValueError(f"unsafe table name: {table!r}")
        row = self.query_one(f"SELECT COUNT(*) AS n FROM {table}")
        return int(row["n"]) if row else 0

    def close(self) -> None:
        existing = getattr(self._local, "conn", None)
        if existing is not None:
            existing.close()
            self._local.conn = None

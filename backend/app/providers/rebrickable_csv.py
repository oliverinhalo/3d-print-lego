"""Set and part provider backed by Rebrickable's public CSV datasets.

Rebrickable publishes its full catalogue as gzipped CSV downloads that need
no API key and no account:  https://rebrickable.com/downloads/

``scripts/bootstrap_data.py`` downloads them and imports them into SQLite;
this provider only reads that index, so a lookup is a few indexed queries
and the application works completely offline afterwards.

Geometry resolution (the important part)
---------------------------------------
An inventory lists *decorated, coloured* parts; a printer needs *shapes*.
``geometry_candidates`` walks Rebrickable's ``part_relationships`` table to
turn a decorated part into the plain part it is printed on:

    3070bpr0056  --(P: print of)-->  3070b
    116173pat0001 --(T: pattern of)--> 116173

and falls back to stripping the ``prNNNN`` / ``patNNNN`` suffix, then to
mould variants (``M``) and alternates (``A``).  Measured against real sets
this lifts LDraw coverage from ~82% to 96-100% of pieces.
"""
from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path

from ..db import Database
from ..models.set import InventoryEntry, LegoSet, SetInventory
from .base import PartProvider, ProviderUnavailable, SetNotFound, SetProvider

#: Relationship types in Rebrickable's part_relationships.csv.
REL_PRINT = "P"      # child is a printed version of parent
REL_PATTERN = "T"    # child is a patterned version of parent
REL_MOULD = "M"      # child is a mould variant of parent
REL_ALTERNATE = "A"  # child is an alternate of parent
REL_PAIR = "R"       # child/parent form a left/right pair

#: "3070bpr0056" -> "3070b", "116173pat0001" -> "116173"
_DECORATION_SUFFIX = re.compile(r"(pr\d{2,}|pat\d{2,}|pb\d{2,}|c\d{2}pr\d+)$", re.IGNORECASE)

#: Parts that are never printable as a physical brick.
_NON_PRINTABLE = re.compile(
    r"\b(sticker|sticker sheet|instruction|box|paper|cardboard|"
    r"decal|manual|poster|booklet|cloth|string|rubber band|sheet)\b",
    re.IGNORECASE,
)

CSV_FILES = ("themes", "colors", "parts", "sets", "inventories",
             "inventory_parts", "part_relationships")


class RebrickableCSVProvider(SetProvider, PartProvider):
    """Reads the imported Rebrickable dataset out of SQLite."""

    name = "rebrickable_csv"

    def __init__(self, db: Database):
        self.db = db

    # --- availability -----------------------------------------------------
    @property
    def available(self) -> bool:
        try:
            return self.db.table_count("sets") > 0
        except sqlite3.Error:
            return False

    def health(self) -> dict:
        try:
            sets = self.db.table_count("sets")
            parts = self.db.table_count("parts")
        except sqlite3.Error:
            sets = parts = 0
        return {
            "name": self.name,
            "available": sets > 0,
            "sets": sets,
            "parts": parts,
            "imported_at": self.db.get_meta("rebrickable_imported_at"),
            "dataset_date": self.db.get_meta("rebrickable_dataset_date"),
        }

    def _require(self) -> None:
        if not self.available:
            raise ProviderUnavailable(
                "The LEGO catalogue has not been imported yet. "
                "Run: python scripts/bootstrap_data.py")

    # --- SetProvider ------------------------------------------------------
    def get_set(self, set_num: str) -> LegoSet:
        self._require()
        row = self.db.query_one(
            "SELECT s.*, t.name AS theme_name FROM sets s "
            "LEFT JOIN themes t ON t.id = s.theme_id WHERE s.set_num = ?",
            (set_num,))
        if row is None:
            # A user typing "77263" means "77263-1"; if that exact inventory
            # version is absent, accept any version of the same number.
            base = set_num.split("-", 1)[0]
            row = self.db.query_one(
                "SELECT s.*, t.name AS theme_name FROM sets s "
                "LEFT JOIN themes t ON t.id = s.theme_id "
                "WHERE s.set_num LIKE ? ORDER BY s.set_num LIMIT 1",
                (f"{base}-%",))
        if row is None:
            raise SetNotFound(f"LEGO set {set_num.split('-')[0]} was not found in the catalogue.")
        return self._row_to_set(row)

    def search(self, text: str, limit: int = 10) -> list[LegoSet]:
        self._require()
        pattern = f"%{text.strip()}%"
        rows = self.db.query(
            "SELECT s.*, t.name AS theme_name FROM sets s "
            "LEFT JOIN themes t ON t.id = s.theme_id "
            "WHERE s.set_num LIKE ? OR s.name LIKE ? "
            "ORDER BY s.year DESC LIMIT ?",
            (pattern, pattern, int(limit)))
        return [self._row_to_set(r) for r in rows]

    def themes(self, limit: int = 60) -> list[dict]:
        """Top-level themes with a set count, for the browse screen."""
        self._require()
        rows = self.db.query(
            "SELECT COALESCE(top.id, t.id) AS id, "
            "       COALESCE(top.name, t.name) AS name, "
            "       COUNT(*) AS sets "
            "FROM sets s "
            "JOIN themes t ON t.id = s.theme_id "
            "LEFT JOIN themes top ON top.id = t.parent_id "
            "WHERE s.num_parts > 0 "
            "GROUP BY COALESCE(top.id, t.id) "
            "ORDER BY sets DESC LIMIT ?", (int(limit),))
        return [{"id": r["id"], "name": r["name"], "sets": r["sets"]} for r in rows]

    def browse(self, *, query: str = "", theme_id: int | None = None,
               year: int | None = None, min_parts: int = 1, max_parts: int = 0,
               sort: str = "popular", limit: int = 24, offset: int = 0) -> dict:
        """Paginated set browsing for the catalogue screen.

        Only sets with a real inventory are listed: a set with no parts data
        cannot be generated, so offering it would be a dead end.
        """
        self._require()
        where = ["s.num_parts >= ?", "EXISTS (SELECT 1 FROM inventories i WHERE i.set_num = s.set_num)"]
        params: list = [max(int(min_parts), 1)]

        if max_parts:
            where.append("s.num_parts <= ?")
            params.append(int(max_parts))
        if query:
            where.append("(s.name LIKE ? OR s.set_num LIKE ?)")
            params += [f"%{query}%", f"%{query}%"]
        if theme_id:
            where.append("(s.theme_id = ? OR t.parent_id = ?)")
            params += [int(theme_id), int(theme_id)]
        if year:
            where.append("s.year = ?")
            params.append(int(year))

        order = {
            "popular": "s.num_parts DESC",
            "newest": "s.year DESC, s.num_parts DESC",
            "smallest": "s.num_parts ASC",
            "name": "s.name ASC",
        }.get(sort, "s.num_parts DESC")

        clause = " AND ".join(where)
        total = self.db.query_one(
            f"SELECT COUNT(*) AS n FROM sets s "
            f"LEFT JOIN themes t ON t.id = s.theme_id WHERE {clause}", tuple(params))

        rows = self.db.query(
            f"SELECT s.*, t.name AS theme_name FROM sets s "
            f"LEFT JOIN themes t ON t.id = s.theme_id "
            f"WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
            (*params, int(limit), int(offset)))

        return {
            "total": int(total["n"]) if total else 0,
            "results": [self._row_to_set(r).to_dict() for r in rows],
        }

    @staticmethod
    def _row_to_set(row: sqlite3.Row) -> LegoSet:
        keys = row.keys()
        return LegoSet(
            set_num=row["set_num"],
            name=row["name"],
            year=row["year"],
            theme=row["theme_name"] if "theme_name" in keys else None,
            num_parts=row["num_parts"] or 0,
            img_url=row["img_url"],
        )

    # --- PartProvider -----------------------------------------------------
    def get_inventory(self, lego_set: LegoSet, include_spares: bool = False) -> SetInventory:
        self._require()
        inv = self.db.query_one(
            "SELECT id FROM inventories WHERE set_num = ? ORDER BY version DESC LIMIT 1",
            (lego_set.set_num,))
        if inv is None:
            raise SetNotFound(
                f"No parts inventory is available for set {lego_set.display_number}.")

        sql = ("SELECT ip.part_num, ip.color_id, ip.quantity, ip.is_spare, ip.img_url, "
               "       p.name AS part_name, c.name AS color_name "
               "FROM inventory_parts ip "
               "LEFT JOIN parts p ON p.part_num = ip.part_num "
               "LEFT JOIN colors c ON c.id = ip.color_id "
               "WHERE ip.inventory_id = ?")
        if not include_spares:
            sql += " AND ip.is_spare = 0"
        rows = self.db.query(sql, (inv["id"],))

        entries = [
            InventoryEntry(
                part_num=r["part_num"],
                name=r["part_name"] or r["part_num"],
                quantity=int(r["quantity"] or 0),
                color_id=r["color_id"],
                color_name=r["color_name"],
                is_spare=bool(r["is_spare"]),
                img_url=r["img_url"],
            )
            for r in rows if int(r["quantity"] or 0) > 0
        ]
        return SetInventory(lego_set=lego_set, entries=entries)

    def is_printable_part(self, part_num: str, name: str) -> bool:
        """Exclude stickers, instructions and other non-moulded inventory rows."""
        return not _NON_PRINTABLE.search(name or "")

    def geometry_candidates(self, part_num: str) -> list[tuple[str, str]]:
        """Return ``(candidate_id, reason)`` pairs, best geometry match first."""
        self._require()
        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(candidate: str | None, reason: str) -> None:
            if candidate and candidate not in seen:
                seen.add(candidate)
                candidates.append((candidate, reason))

        add(part_num, "direct")

        # Follow print/pattern chains up to the undecorated ancestor.
        current = part_num
        for _ in range(4):
            parent = self._parent(current, (REL_PRINT, REL_PATTERN))
            if not parent or parent == current:
                break
            add(parent, "print_parent")
            current = parent

        # Suffix stripping catches decorated parts missing from the relationships.
        for base, _reason in list(candidates):
            match = _DECORATION_SUFFIX.search(base)
            if match:
                add(base[:match.start()], "suffix_stripped")

        # Mould variants and alternates share printable geometry closely enough.
        for base, _reason in list(candidates):
            add(self._parent(base, (REL_MOULD,)), "mould_variant")
            add(self._parent(base, (REL_ALTERNATE,)), "alternate")

        # Trailing letters mark mould revisions ("3062b" -> "3062"); try last.
        for base, _reason in list(candidates):
            stripped = re.sub(r"[a-z]$", "", base)
            if stripped != base:
                add(stripped, "mould_revision")

        return candidates

    def _parent(self, child: str, rel_types: tuple[str, ...]) -> str | None:
        placeholders = ",".join("?" * len(rel_types))
        row = self.db.query_one(
            f"SELECT parent_part_num FROM part_relationships "
            f"WHERE child_part_num = ? AND rel_type IN ({placeholders}) LIMIT 1",
            (child, *rel_types))
        return row["parent_part_num"] if row else None

    def part_name(self, part_num: str) -> str | None:
        row = self.db.query_one("SELECT name FROM parts WHERE part_num = ?", (part_num,))
        return row["name"] if row else None


# --- dataset import -------------------------------------------------------

def import_csv_directory(db: Database, directory: Path, *, progress=None) -> dict[str, int]:
    """Import the Rebrickable CSV files in ``directory`` into SQLite.

    Idempotent: each table is replaced wholesale, so re-running refreshes the
    catalogue after downloading a newer dataset snapshot.
    """
    directory = Path(directory)
    counts: dict[str, int] = {}

    loaders = {
        "themes": ("themes", ("id", "name", "parent_id"), _row_themes),
        "colors": ("colors", ("id", "name", "rgb", "is_trans"), _row_colors),
        "parts": ("parts", ("part_num", "name", "part_cat_id", "part_material"), _row_parts),
        "sets": ("sets", ("set_num", "name", "year", "theme_id", "num_parts", "img_url"),
                 _row_sets),
        "inventories": ("inventories", ("id", "version", "set_num"), _row_inventories),
        "inventory_parts": ("inventory_parts",
                            ("inventory_id", "part_num", "color_id", "quantity",
                             "is_spare", "img_url"), _row_inventory_parts),
        "part_relationships": ("part_relationships",
                               ("rel_type", "child_part_num", "parent_part_num"),
                               _row_relationships),
    }

    for stem, (table, columns, mapper) in loaders.items():
        path = directory / f"{stem}.csv"
        if not path.exists():
            continue
        placeholders = ",".join("?" * len(columns))
        sql = f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
        total = 0
        with db.connect() as conn:
            conn.execute(f"DELETE FROM {table}")
            with open(path, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                batch: list[tuple] = []
                for row in reader:
                    try:
                        batch.append(mapper(row))
                    except (KeyError, ValueError):
                        continue
                    if len(batch) >= 10_000:
                        conn.executemany(sql, batch)
                        total += len(batch)
                        batch.clear()
                if batch:
                    conn.executemany(sql, batch)
                    total += len(batch)
        counts[table] = total
        if progress:
            progress(table, total)
    return counts


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value) -> int:
    return 1 if str(value).strip().lower() in ("t", "true", "1", "yes") else 0


def _row_themes(r): return (_int(r["id"]), r["name"], _int(r.get("parent_id")))
def _row_colors(r): return (_int(r["id"]), r["name"], r.get("rgb"), r.get("is_trans"))
def _row_parts(r): return (r["part_num"], r["name"], _int(r.get("part_cat_id")),
                           r.get("part_material"))
def _row_sets(r): return (r["set_num"], r["name"], _int(r.get("year")),
                          _int(r.get("theme_id")), _int(r.get("num_parts"), 0),
                          r.get("img_url"))
def _row_inventories(r): return (_int(r["id"]), _int(r["version"], 1), r["set_num"])
def _row_inventory_parts(r): return (_int(r["inventory_id"]), r["part_num"],
                                     _int(r.get("color_id")), _int(r["quantity"], 0),
                                     _bool(r.get("is_spare")), r.get("img_url"))
def _row_relationships(r): return (r["rel_type"], r["child_part_num"], r["parent_part_num"])

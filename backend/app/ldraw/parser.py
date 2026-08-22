"""LDraw ``.dat`` parser producing triangle meshes.

The LDraw format is a small recursive scene description:

* line type ``1`` references another file with a 3x3 matrix + translation;
* line types ``3`` / ``4`` are triangles and quads;
* line types ``2`` / ``5`` are edge lines and are ignored (no surface);
* ``0 BFC`` meta commands declare face winding and can invert a subfile.

Two details matter for producing printable solids:

**Winding.**  A face's orientation depends on the file's declared winding
(``CW``/``CCW``), any ``INVERTNEXT`` applied by the parent, and the sign of
the accumulated transform determinant (a mirrored matrix flips handedness).

**Caching.**  A part like a wheel references the same primitive hundreds of
times.  Sub-files are therefore parsed once into their *own* coordinate
system, assuming a positive-determinant embedding, and reused.  The caller
applies the instance transform and reverses winding when the determinant is
negative.  This turns an exponential blow-up into a near-linear walk.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from .mesh import Mesh, Triangle, Vec3

# A 3x4 affine transform stored row-major: 9 rotation terms then translation.
Matrix = tuple[float, ...]
IDENTITY: Matrix = (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)

MAX_DEPTH = 48


class LDrawError(RuntimeError):
    """Raised when a part cannot be parsed or is not present in the library."""


def compose(a: Matrix, b: Matrix) -> Matrix:
    """Return ``a * b`` for two affine transforms."""
    out = [0.0] * 12
    for i in range(3):
        for j in range(3):
            out[i * 3 + j] = (a[i * 3] * b[j] + a[i * 3 + 1] * b[3 + j] + a[i * 3 + 2] * b[6 + j])
        out[9 + i] = (a[i * 3] * b[9] + a[i * 3 + 1] * b[10] + a[i * 3 + 2] * b[11] + a[9 + i])
    return tuple(out)


def determinant(m: Matrix) -> float:
    return (m[0] * (m[4] * m[8] - m[5] * m[7])
            - m[1] * (m[3] * m[8] - m[5] * m[6])
            + m[2] * (m[3] * m[7] - m[4] * m[6]))


def apply(m: Matrix, v: Vec3) -> Vec3:
    x, y, z = v
    return (m[0] * x + m[1] * y + m[2] * z + m[9],
            m[3] * x + m[4] * y + m[5] * z + m[10],
            m[6] * x + m[7] * y + m[8] * z + m[11])


@dataclass(slots=True)
class ParseStats:
    subfiles: int = 0
    quads: int = 0
    tris: int = 0
    uncertified: int = 0
    missing: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.missing is None:
            self.missing = []

    def merge(self, other: "ParseStats") -> None:
        """Fold a sub-file's statistics into this one.

        Sub-file parses are cached, so a cache hit must still contribute its
        statistics — otherwise a missing-part reference would be reported the
        first time a shape is built and silently vanish afterwards.
        """
        self.subfiles += other.subfiles
        self.quads += other.quads
        self.tris += other.tris
        self.uncertified += other.uncertified
        self.missing.extend(other.missing)


class LDrawLibrary:
    """Indexed, thread-safe accessor for an unpacked LDraw parts library."""

    #: search order matches the official LDraw file-resolution rules
    SUBDIRS = ("parts", "p", "models")

    def __init__(self, root: Path):
        root = Path(root)
        # Accept either .../ldraw or a directory containing ldraw/
        if not (root / "parts").is_dir() and (root / "ldraw" / "parts").is_dir():
            root = root / "ldraw"
        self.root = root
        self._index: dict[str, Path] = {}
        self._cache: dict[tuple[str, bool], tuple[list[Triangle], ParseStats]] = {}
        self._lock = threading.RLock()
        if (root / "parts").is_dir():
            self._build_index()

    # --- library index ----------------------------------------------------
    def _build_index(self) -> None:
        """Map lowercase relative names (and bare filenames) to real paths."""
        index: dict[str, Path] = {}
        for sub in self.SUBDIRS:
            base = self.root / sub
            if not base.is_dir():
                continue
            for dirpath, _dirs, files in os.walk(base):
                rel_dir = Path(dirpath).relative_to(self.root)
                for fn in files:
                    if not fn.lower().endswith(".dat"):
                        continue
                    full = Path(dirpath) / fn
                    rel = (rel_dir / fn).as_posix().lower()
                    index.setdefault(rel, full)
                    # also index without the leading top-level directory,
                    # because references are written as "s\3001s01.dat"
                    parts_rel = rel.split("/", 1)[-1]
                    index.setdefault(parts_rel, full)
                    index.setdefault(fn.lower(), full)
        self._index = index

    @property
    def available(self) -> bool:
        return bool(self._index)

    def part_count(self) -> int:
        base = self.root / "parts"
        if not base.is_dir():
            return 0
        return sum(1 for f in os.listdir(base) if f.lower().endswith(".dat"))

    def has_part(self, part_id: str) -> bool:
        return (self.root / "parts" / f"{part_id}.dat").is_file()

    def resolve(self, reference: str) -> Path | None:
        name = reference.replace("\\", "/").strip().lower()
        hit = self._index.get(name)
        if hit:
            return hit
        return self._index.get(name.rsplit("/", 1)[-1])

    def part_title(self, part_id: str) -> str | None:
        path = self.resolve(f"parts/{part_id}.dat") or self.resolve(f"{part_id}.dat")
        if not path:
            return None
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                first = fh.readline().strip()
        except OSError:
            return None
        return first[1:].strip() if first.startswith("0") else None

    # --- parsing ----------------------------------------------------------
    def _parse_file(self, path: Path, invert: bool, depth: int,
                    stats: ParseStats) -> list[Triangle]:
        """Parse ``path`` into triangles in its own coordinate system.

        Winding is normalised for a positive-determinant embedding; the
        result is cached per ``(path, invert)``.
        """
        key = (str(path), invert)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            triangles, cached_stats = cached
            stats.merge(cached_stats)
            return triangles
        if depth > MAX_DEPTH:
            raise LDrawError(f"LDraw recursion deeper than {MAX_DEPTH} in {path.name}")

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise LDrawError(f"cannot read {path}: {exc}") from exc

        local = ParseStats()
        out: list[Triangle] = []
        winding = "CCW"
        certified: bool | None = None
        invert_next = False

        for raw in text.splitlines():
            tokens = raw.split()
            if not tokens:
                continue
            code = tokens[0]

            if code == "0":
                if len(tokens) > 1 and tokens[1] == "BFC":
                    for tok in tokens[2:]:
                        if tok == "CW":
                            winding = "CW"
                        elif tok == "CCW":
                            winding = "CCW"
                        elif tok == "INVERTNEXT":
                            invert_next = True
                        elif tok == "CERTIFY":
                            certified = True
                        elif tok == "NOCERTIFY":
                            certified = False
                continue

            if code == "1":
                if len(tokens) < 15:
                    continue
                try:
                    n = [float(t) for t in tokens[2:14]]
                except ValueError:
                    continue
                sub_matrix: Matrix = (n[3], n[4], n[5], n[6], n[7], n[8],
                                      n[9], n[10], n[11], n[0], n[1], n[2])
                reference = " ".join(tokens[14:])
                child_path = self.resolve(reference)
                if child_path is None:
                    local.missing.append(reference)
                    invert_next = False
                    continue
                child_invert = invert ^ invert_next
                if determinant(sub_matrix) < 0:
                    # a mirrored instance flips the child's handedness
                    child_invert = not child_invert
                child_tris = self._parse_file(child_path, child_invert, depth + 1, local)
                for tri in child_tris:
                    out.append(tuple(apply(sub_matrix, v) for v in tri))  # type: ignore[arg-type]
                local.subfiles += 1
                invert_next = False
                continue

            if code in ("3", "4"):
                count = 3 if code == "3" else 4
                need = 2 + count * 3
                if len(tokens) < need:
                    invert_next = False
                    continue
                try:
                    nums = [float(t) for t in tokens[2:need]]
                except ValueError:
                    invert_next = False
                    continue
                pts: list[Vec3] = [
                    (nums[i * 3], nums[i * 3 + 1], nums[i * 3 + 2]) for i in range(count)
                ]
                reverse = (winding == "CW") ^ invert
                faces = ((0, 1, 2),) if count == 3 else ((0, 1, 2), (0, 2, 3))
                for f in faces:
                    tri = [pts[f[0]], pts[f[1]], pts[f[2]]]
                    if reverse:
                        tri.reverse()
                    out.append(tuple(tri))  # type: ignore[arg-type]
                if count == 4:
                    local.quads += 1
                else:
                    local.tris += 1
                invert_next = False
                continue

            # line types 2 (edge) and 5 (optional edge) carry no surface
            invert_next = False

        if certified is False:
            local.uncertified += 1

        with self._lock:
            self._cache[key] = (out, local)
        stats.merge(local)
        return out

    def load_part(self, part_id: str) -> tuple[Mesh, ParseStats]:
        """Load a part by LDraw id (e.g. ``"3001"``) as a mesh in LDU."""
        path = self.resolve(f"parts/{part_id}.dat") or self.resolve(f"{part_id}.dat")
        if path is None:
            raise LDrawError(f"part {part_id!r} is not in the LDraw library")
        stats = ParseStats()
        triangles = self._parse_file(path, invert=False, depth=0, stats=stats)
        if not triangles:
            raise LDrawError(f"part {part_id!r} produced no geometry")
        return Mesh(list(triangles)), stats

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

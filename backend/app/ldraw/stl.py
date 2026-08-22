"""STL reading/writing and geometry validation.

STL carries no unit information, so everything written here is millimetres by
convention: LDraw units are multiplied by ``ldu_mm`` (0.4) during conversion
and the result is checked against plausible LEGO dimensions.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .mesh import Mesh, Triangle

BINARY_HEADER = 80
TRIANGLE_RECORD = 50  # 12 floats + 2 byte attribute


def _normal(tri: Triangle) -> tuple[float, float, float]:
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tri
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length == 0.0 or not math.isfinite(length):
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def write_binary_stl(mesh: Mesh, path: Path, header: str = "") -> int:
    """Write ``mesh`` as binary STL. Returns bytes written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    head = header.encode("ascii", "replace")[:BINARY_HEADER]
    head = head.ljust(BINARY_HEADER, b"\0")
    with open(path, "wb") as fh:
        fh.write(head)
        fh.write(struct.pack("<I", len(mesh.triangles)))
        pack = struct.Struct("<12fH").pack
        for tri in mesh.triangles:
            nx, ny, nz = _normal(tri)
            fh.write(pack(nx, ny, nz,
                          tri[0][0], tri[0][1], tri[0][2],
                          tri[1][0], tri[1][1], tri[1][2],
                          tri[2][0], tri[2][1], tri[2][2], 0))
    return path.stat().st_size


def write_ascii_stl(mesh: Mesh, path: Path, name: str = "part") -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "_-") or "part"
    with open(path, "w", encoding="ascii", errors="replace") as fh:
        fh.write(f"solid {safe}\n")
        for tri in mesh.triangles:
            nx, ny, nz = _normal(tri)
            fh.write(f"  facet normal {nx:.6e} {ny:.6e} {nz:.6e}\n    outer loop\n")
            for v in tri:
                fh.write(f"      vertex {v[0]:.6e} {v[1]:.6e} {v[2]:.6e}\n")
            fh.write("    endloop\n  endfacet\n")
        fh.write(f"endsolid {safe}\n")
    return path.stat().st_size


def read_stl(path: Path) -> Mesh:
    """Read binary or ASCII STL into a :class:`Mesh`."""
    path = Path(path)
    data = path.read_bytes()
    if _looks_ascii(data):
        return _read_ascii(data)
    return _read_binary(data)


def _looks_ascii(data: bytes) -> bool:
    if data[:5].lower() != b"solid":
        return False
    # A binary file may still start with "solid"; the length check decides.
    if len(data) >= BINARY_HEADER + 4:
        (count,) = struct.unpack("<I", data[BINARY_HEADER:BINARY_HEADER + 4])
        if len(data) == BINARY_HEADER + 4 + count * TRIANGLE_RECORD:
            return False
    return b"facet" in data[:2048].lower()


def _read_binary(data: bytes) -> Mesh:
    if len(data) < BINARY_HEADER + 4:
        raise ValueError("STL too short to contain a header")
    (count,) = struct.unpack("<I", data[BINARY_HEADER:BINARY_HEADER + 4])
    expected = BINARY_HEADER + 4 + count * TRIANGLE_RECORD
    if len(data) != expected:
        raise ValueError(
            f"STL length {len(data)} does not match declared {count} triangles "
            f"(expected {expected} bytes)")
    tris: list[Triangle] = []
    unpack = struct.Struct("<12fH").unpack_from
    off = BINARY_HEADER + 4
    for _ in range(count):
        f = unpack(data, off)
        tris.append(((f[3], f[4], f[5]), (f[6], f[7], f[8]), (f[9], f[10], f[11])))
        off += TRIANGLE_RECORD
    return Mesh(tris)


def _read_ascii(data: bytes) -> Mesh:
    tris: list[Triangle] = []
    verts: list[tuple[float, float, float]] = []
    for line in data.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == "vertex":
            verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            if len(verts) == 3:
                tris.append((verts[0], verts[1], verts[2]))
                verts = []
    if not tris:
        raise ValueError("ASCII STL contained no triangles")
    return Mesh(tris)


# --- validation -----------------------------------------------------------

#: Smallest / largest plausible bounding-box edge for a LEGO element, in mm.
#: A 1x1 tile is 8 x 8 x 3.2 mm and its smallest feature ~1 mm; the largest
#: single moulded elements (baseplates, large panels) reach ~500 mm.
MIN_DIMENSION_MM = 0.5
MAX_DIMENSION_MM = 800.0
MIN_FILE_BYTES = BINARY_HEADER + 4 + TRIANGLE_RECORD


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    triangles: int = 0
    dimensions: tuple[float, float, float] = (0.0, 0.0, 0.0)
    size_bytes: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "triangles": self.triangles,
            "dimensions_mm": list(self.dimensions),
            "size_bytes": self.size_bytes,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def validate_mesh(mesh: Mesh, *, size_bytes: int = 0,
                  require_manifold: bool = False) -> ValidationResult:
    """Check a mesh is a plausible, printable LEGO element."""
    errors: list[str] = []
    warnings: list[str] = []

    if len(mesh) == 0:
        errors.append("mesh contains no triangles")
        return ValidationResult(False, 0, (0.0, 0.0, 0.0), size_bytes, errors, warnings)

    if mesh.has_non_finite():
        # Bail out here: every measurement below (bounds, edge welding) is
        # meaningless once a coordinate is NaN or infinite, and some of it
        # cannot even be computed.
        errors.append("mesh contains NaN or infinite coordinates")
        return ValidationResult(False, len(mesh), (0.0, 0.0, 0.0),
                                size_bytes, errors, warnings)

    dims = mesh.dimensions()
    longest = max(dims)
    if longest < MIN_DIMENSION_MM:
        errors.append(
            f"model is implausibly small ({dims[0]:.4f} x {dims[1]:.4f} x "
            f"{dims[2]:.4f} mm) — likely a unit-scale error")
    elif longest > MAX_DIMENSION_MM:
        errors.append(
            f"model is implausibly large ({dims[0]:.1f} x {dims[1]:.1f} x "
            f"{dims[2]:.1f} mm) — likely a unit-scale error")

    if min(dims) <= 0.0:
        warnings.append("model is flat in at least one axis")

    degenerate = mesh.degenerate_count()
    if degenerate:
        warnings.append(f"{degenerate} degenerate (zero-area) triangles")
        if degenerate == len(mesh):
            errors.append("every triangle is degenerate")

    boundary, non_manifold = mesh.edge_manifold_report()
    if boundary or non_manifold:
        message = (f"mesh is not closed: {boundary} boundary edges, "
                   f"{non_manifold} non-manifold edges")
        # LDraw parts are surface models; small gaps are common and every
        # modern slicer repairs them, so this is only fatal on request.
        (errors if require_manifold else warnings).append(message)

    if size_bytes and size_bytes < MIN_FILE_BYTES:
        errors.append(f"file is too small to be a valid STL ({size_bytes} bytes)")

    return ValidationResult(not errors, len(mesh), dims, size_bytes, errors, warnings)


def validate_stl_file(path: Path, *, require_manifold: bool = False) -> ValidationResult:
    """Validate an STL on disk, structure included."""
    path = Path(path)
    if not path.exists():
        return ValidationResult(False, errors=[f"file does not exist: {path}"])
    size = path.stat().st_size
    if size < MIN_FILE_BYTES:
        return ValidationResult(False, size_bytes=size,
                                errors=[f"file is too small to be a valid STL ({size} bytes)"])
    try:
        mesh = read_stl(path)
    except (ValueError, struct.error) as exc:
        return ValidationResult(False, size_bytes=size,
                                errors=[f"corrupt STL structure: {exc}"])
    return validate_mesh(mesh, size_bytes=size, require_manifold=require_manifold)

"""A minimal triangle mesh with the operations this pipeline needs.

Deliberately dependency-free: the whole conversion path is pure Python so the
backend installs cleanly on a small home server without a compiler toolchain.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]
Triangle = tuple[Vec3, Vec3, Vec3]


@dataclass(slots=True)
class Mesh:
    triangles: list[Triangle] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.triangles)

    # --- measurement ------------------------------------------------------
    def bounds(self) -> tuple[Vec3, Vec3]:
        """Axis-aligned bounding box as ``(min, max)``."""
        if not self.triangles:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        for tri in self.triangles:
            for v in tri:
                for i in range(3):
                    if v[i] < lo[i]:
                        lo[i] = v[i]
                    if v[i] > hi[i]:
                        hi[i] = v[i]
        return tuple(lo), tuple(hi)  # type: ignore[return-value]

    def dimensions(self) -> Vec3:
        lo, hi = self.bounds()
        return (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])

    def volume(self) -> float:
        """Signed volume enclosed by the surface, via the divergence theorem.

        Positive for outward-facing triangles. Used for filament estimates,
        so the absolute value is what callers want.
        """
        total = 0.0
        for a, b, c in self.triangles:
            total += (a[0] * (b[1] * c[2] - b[2] * c[1])
                      - a[1] * (b[0] * c[2] - b[2] * c[0])
                      + a[2] * (b[0] * c[1] - b[1] * c[0]))
        return total / 6.0

    def surface_area(self) -> float:
        """Total triangle area."""
        total = 0.0
        for a, b, c in self.triangles:
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            total += math.sqrt(cx * cx + cy * cy + cz * cz) / 2.0
        return total

    def has_non_finite(self) -> bool:
        return any(not math.isfinite(c) for tri in self.triangles for v in tri for c in v)

    def degenerate_count(self, eps: float = 1e-12) -> int:
        """Triangles with (near) zero area — harmless but worth counting."""
        n = 0
        for a, b, c in self.triangles:
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            if (cx * cx + cy * cy + cz * cz) <= eps:
                n += 1
        return n

    def edge_manifold_report(self) -> tuple[int, int]:
        """Return ``(boundary_edges, non_manifold_edges)``.

        Vertices are snapped to a 1e-4 mm grid first: LDraw primitives are
        generated analytically, so matching edges agree to many decimals but
        rarely bit-for-bit.
        """
        counts: dict[tuple, int] = {}

        def key(v: Vec3) -> tuple[int, int, int]:
            # Guard against non-finite input: callers should reject such a
            # mesh first, but this must never raise.
            return tuple(
                round(c * 1e4) if math.isfinite(c) else 0 for c in v)  # type: ignore[return-value]

        for tri in self.triangles:
            k = [key(v) for v in tri]
            for i in range(3):
                a, b = k[i], k[(i + 1) % 3]
                edge = (a, b) if a <= b else (b, a)
                counts[edge] = counts.get(edge, 0) + 1
        boundary = sum(1 for c in counts.values() if c == 1)
        non_manifold = sum(1 for c in counts.values() if c > 2)
        return boundary, non_manifold

    # --- transforms -------------------------------------------------------
    def scaled(self, factor: float) -> "Mesh":
        if factor == 1.0:
            return self
        return Mesh([tuple((v[0] * factor, v[1] * factor, v[2] * factor) for v in tri)  # type: ignore[misc]
                     for tri in self.triangles])

    def translated(self, dx: float, dy: float, dz: float) -> "Mesh":
        return Mesh([tuple((v[0] + dx, v[1] + dy, v[2] + dz) for v in tri)  # type: ignore[misc]
                     for tri in self.triangles])

    def rotated_x90(self, turns: int = 1) -> "Mesh":
        """Rotate about X in 90 degree steps (exact, no trig error)."""
        turns %= 4
        if turns == 0:
            return self
        out: list[Triangle] = []
        for tri in self.triangles:
            new = []
            for x, y, z in tri:
                for _ in range(turns):
                    y, z = -z, y
                new.append((x, y, z))
            out.append(tuple(new))  # type: ignore[arg-type]
        return Mesh(out)

    def rotated_y90(self, turns: int = 1) -> "Mesh":
        turns %= 4
        if turns == 0:
            return self
        out: list[Triangle] = []
        for tri in self.triangles:
            new = []
            for x, y, z in tri:
                for _ in range(turns):
                    x, z = z, -x
                new.append((x, y, z))
            out.append(tuple(new))  # type: ignore[arg-type]
        return Mesh(out)

    def dropped_to_origin(self) -> "Mesh":
        """Centre on XY and rest the mesh on the Z=0 plane."""
        lo, hi = self.bounds()
        return self.translated(-(lo[0] + hi[0]) / 2.0, -(lo[1] + hi[1]) / 2.0, -lo[2])

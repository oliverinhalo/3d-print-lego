"""LDraw part -> printable STL conversion pipeline.

    LDraw .dat  ->  parse  ->  normalise units  ->  orient  ->  validate  ->  STL

Coordinate systems differ between the two worlds:

* LDraw is Y-down (-Y is up) and measured in LDU (1 LDU = 0.4 mm);
* slicers are Z-up and measured in millimetres.

The conversion applies ``(x, y, z)_ldraw -> (x, z, -y)_print`` and scales by
``ldu_mm``, which puts a 2x4 brick at exactly 32.00 x 16.00 x 11.20 mm.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .mesh import Mesh
from .parser import LDrawLibrary, ParseStats
from .stl import ValidationResult, validate_mesh, write_ascii_stl, write_binary_stl


def ldraw_to_print_space(mesh: Mesh, ldu_mm: float = 0.4, scale: float = 1.0) -> Mesh:
    """Convert LDraw coordinates (Y-down, LDU) to print space (Z-up, mm)."""
    factor = ldu_mm * scale
    return Mesh([
        tuple((v[0] * factor, v[2] * factor, -v[1] * factor) for v in tri)  # type: ignore[misc]
        for tri in mesh.triangles
    ])


def orient_for_printing(mesh: Mesh, strategy: str = "native") -> Mesh:
    """Place the part on the build plate.

    ``native`` (the default) keeps LDraw's own orientation and simply rests
    the part on Z=0.  That is already the right answer for LEGO: elements are
    modelled with their flat underside down and studs up, which is the most
    sensible printable face and needs no support under the studs.  Rotating
    to minimise height would tip a 1x1 brick onto its side and put the stud
    on an overhang, so we deliberately do not do that.

    ``flat`` lays the part on its largest footprint instead.  It is available
    for unusual geometry but is not the default, and neither strategy alters
    the shape itself — only its placement.

    Either way the slicer's Auto Arrange still decides the final layout.
    """
    if strategy == "flat":
        best = None
        best_score = None
        for candidate in (mesh, mesh.rotated_x90(1), mesh.rotated_y90(1)):
            dx, dy, dz = candidate.dimensions()
            score = (round(dz, 6), -round(dx * dy, 6))
            if best_score is None or score < best_score:
                best, best_score = candidate, score
        mesh = best or mesh
    return mesh.dropped_to_origin()


@dataclass(slots=True)
class ConversionResult:
    mesh: Mesh
    validation: ValidationResult
    stats: ParseStats
    size_bytes: int = 0

    @property
    def ok(self) -> bool:
        return self.validation.ok


def convert_part(library: LDrawLibrary, part_id: str, destination: Path | None = None,
                 *, ldu_mm: float = 0.4, scale: float = 1.0,
                 auto_orient: bool = True, orient_strategy: str = "native",
                 binary: bool = True,
                 require_manifold: bool = False) -> ConversionResult:
    """Run the full pipeline for one LDraw part."""
    raw, stats = library.load_part(part_id)
    mesh = ldraw_to_print_space(raw, ldu_mm=ldu_mm, scale=scale)
    if auto_orient:
        mesh = orient_for_printing(mesh, orient_strategy)
    validation = validate_mesh(mesh, require_manifold=require_manifold)

    size = 0
    if destination is not None and validation.ok:
        title = library.part_title(part_id) or part_id
        if binary:
            size = write_binary_stl(mesh, destination, header=f"{part_id} {title} (LDraw, mm)")
        else:
            size = write_ascii_stl(mesh, destination, name=part_id)
        validation.size_bytes = size
    return ConversionResult(mesh, validation, stats, size)

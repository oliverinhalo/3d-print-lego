"""3MF export: one file per build plate, already arranged.

Why 3MF rather than more STLs
-----------------------------
An STL holds one object, so a 350-piece set means 350 files to select and
import, and the slicer must then arrange them itself. 3MF holds many objects
*with their positions*, so a plate opens ready to slice.

It also collapses the duplication. A set with 25 identical 1x2 plates stores
that mesh **once** and references it 25 times through build items with
different transforms — the file stays small no matter how repetitive the set
is, where 25 STLs would repeat every triangle 25 times.

What is written
---------------
A minimal, standard-conformant 3MF (an OPC/ZIP package)::

    [Content_Types].xml
    _rels/.rels
    3D/3dmodel.model

Deliberately no slicer-specific metadata: the core format is what every
slicer reads, so the same file works in Bambu Studio, OrcaSlicer, PrusaSlicer
and Cura.
"""
from __future__ import annotations

import xml.sax.saxutils as saxutils
import zipfile
from pathlib import Path

from ..ldraw.mesh import Mesh
from ..ldraw.stl import read_stl
from .plate_service import Plate

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
MODEL_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
MODEL_CONTENT_TYPE = "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="{content_type}"/>
 <Default Extension="png" ContentType="image/png"/>
</Types>
""".format(content_type=MODEL_CONTENT_TYPE)

RELS = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rel0" Target="/3D/3dmodel.model" Type="{MODEL_REL}"/>
</Relationships>
"""

#: Vertices closer than this are treated as one. STL repeats every shared
#: vertex per triangle, so welding cuts the vertex count roughly six-fold.
WELD_TOLERANCE = 1e-4


def _weld(mesh: Mesh) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    """Collapse duplicated STL vertices into an indexed mesh."""
    index: dict[tuple[int, int, int], int] = {}
    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    scale = 1.0 / WELD_TOLERANCE

    for tri in mesh.triangles:
        corners = []
        for vertex in tri:
            key = (round(vertex[0] * scale), round(vertex[1] * scale),
                   round(vertex[2] * scale))
            found = index.get(key)
            if found is None:
                found = len(vertices)
                index[key] = found
                vertices.append(vertex)
            corners.append(found)
        # A triangle collapsed by welding has no area; 3MF rejects those.
        if corners[0] != corners[1] and corners[1] != corners[2] and corners[0] != corners[2]:
            triangles.append((corners[0], corners[1], corners[2]))
    return vertices, triangles


def _number(value: float) -> str:
    """Compact fixed-point output; 3MF is millimetres so 4dp is ample."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _transform(x: float, y: float, z: float, rotated: bool) -> str:
    """3MF transform: a 4x3 matrix, row-major, translation in the last row."""
    if rotated:                       # 90 degrees about Z
        rows = ("0", "1", "0", "-1", "0", "0", "0", "0", "1")
    else:
        rows = ("1", "0", "0", "0", "1", "0", "0", "0", "1")
    return " ".join([*rows, _number(x), _number(y), _number(z)])


def write_plate_3mf(plate: Plate, geometry_paths: dict[str, Path], destination: Path,
                    *, title: str = "") -> int:
    """Write one plate as a 3MF. Returns the file size in bytes.

    ``geometry_paths`` maps a geometry key to the cached STL for that shape.
    Each distinct shape becomes one ``<object>``; each placed piece becomes
    one ``<item>`` referencing it.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    # One object per distinct shape actually used on this plate.
    used_keys: list[str] = []
    for placement in plate.placements:
        key = placement.item.geometry_key
        if key not in used_keys and key in geometry_paths:
            used_keys.append(key)

    object_ids: dict[str, int] = {key: number for number, key in enumerate(used_keys, start=1)}

    parts: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>\n',
                        f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}">\n',
                        ' <metadata name="Application">Brick Foundry</metadata>\n']
    if title:
        parts.append(f' <metadata name="Title">{saxutils.escape(title)}</metadata>\n')
    parts.append(' <resources>\n')

    for key in used_keys:
        mesh = read_stl(geometry_paths[key])
        vertices, triangles = _weld(mesh)
        if not triangles:
            continue
        name = saxutils.quoteattr(_object_name(plate, key))
        parts.append(f'  <object id="{object_ids[key]}" type="model" name={name}>\n   <mesh>\n    <vertices>\n')
        parts.extend(
            f'     <vertex x="{_number(v[0])}" y="{_number(v[1])}" z="{_number(v[2])}"/>\n'
            for v in vertices)
        parts.append('    </vertices>\n    <triangles>\n')
        parts.extend(
            f'     <triangle v1="{t[0]}" v2="{t[1]}" v3="{t[2]}"/>\n' for t in triangles)
        parts.append('    </triangles>\n   </mesh>\n  </object>\n')

    parts.append(' </resources>\n <build>\n')
    for placement in plate.placements:
        object_id = object_ids.get(placement.item.geometry_key)
        if object_id is None:
            continue
        transform = _transform(placement.x, placement.y, 0.0, placement.rotated)
        parts.append(f'  <item objectid="{object_id}" transform="{transform}"/>\n')
    parts.append(' </build>\n</model>\n')

    model_xml = "".join(parts)

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("3D/3dmodel.model", model_xml)

    return destination.stat().st_size


def _object_name(plate: Plate, geometry_key: str) -> str:
    for placement in plate.placements:
        if placement.item.geometry_key == geometry_key:
            return f"{placement.item.part_num} {placement.item.name}"[:96]
    return geometry_key


def plate_filename(plate: Plate, total: int) -> str:
    """``Plate_01_Red.3mf`` — index first so files sort in print order."""
    from .normalize import sanitize_filename

    width = max(2, len(str(total)))
    stem = f"Plate_{plate.index:0{width}d}"
    if plate.group:
        stem = f"{stem}_{sanitize_filename(plate.group, max_length=24, fallback='')}"
    return f"{stem}.3mf"

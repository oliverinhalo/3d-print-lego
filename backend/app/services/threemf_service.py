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

Two shapes of output are available:

* **one file per plate** — plain core 3MF, no slicer-specific metadata, so it
  opens the same way in Bambu Studio, OrcaSlicer, PrusaSlicer and Cura;
* **one project holding every plate** — the same core geometry plus Bambu's
  ``Metadata/model_settings.config``, which names each plate (by colour) and
  lists the instances on it.

For the project file the plate layout matters as much as the metadata:
Bambu Studio assigns an instance to a plate by *where it is in world space*
(``PartPlateList::reload_all_objects`` intersects each instance against each
plate), so plate N's contents have to be written at plate N's world origin.
That origin is reproduced from the slicer's own arithmetic — see
``plate_origin`` below.
"""
from __future__ import annotations

import json
import math
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

#: Gap between plates in the slicer's world grid, as a fraction of bed size.
#: From PartPlate.hpp: LOGICAL_PART_PLATE_GAP = 1/5.
PLATE_GAP_FRACTION = 1.0 / 5.0

#: Bambu Studio refuses to hold more than this many plates in one project.
MAX_PROJECT_PLATES = 36

#: Generator string the project file must carry.
#:
#: Bambu Studio only treats a 3MF as a *project* when the model's
#: "Application" metadata begins with "BambuStudio-" or "OrcaSlicer-"
#: (_BBS_3MF_Importer::_handle_end_metadata sets m_is_bbl_3mf there, and
#: nowhere else). Without that flag it ignores model_settings.config
#: completely: you get one unnamed plate, and every repeated instance is
#: split into a separate object.
#:
#: So the string is a compatibility requirement, not a claim of authorship —
#: OrcaSlicer writes the same "BambuStudio-" prefix for exactly this reason.
#: The real generator is recorded in the Title and Description metadata
#: alongside it.
BAMBU_GENERATOR = "BambuStudio-2.3.0"

#: Value of the BambuStudio:3mfVersion metadata (VERSION_BBS_3MF).
BAMBU_3MF_VERSION = "1"


def plate_columns(count: int) -> int:
    """Columns in the slicer's plate grid, matching ``compute_colum_count``.

    Reproduced from PartPlate.hpp so our world offsets land on the same grid
    the slicer builds; a mismatch would drop parts onto the wrong plate.
    """
    if count <= 0:
        return 1
    value = math.sqrt(count)
    # C's round() goes half away from zero, unlike Python's banker's rounding.
    rounded = math.floor(value + 0.5)
    return int(rounded + 1) if value > rounded else int(rounded)


def plate_origin(index: int, columns: int,
                 bed_width: float, bed_depth: float) -> tuple[float, float]:
    """World-space corner of plate ``index`` (0-based), as the slicer places it."""
    row, column = divmod(index, max(columns, 1))
    return (column * bed_width * (1.0 + PLATE_GAP_FRACTION),
            -row * bed_depth * (1.0 + PLATE_GAP_FRACTION))


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


def _mesh_resources(keys: list[str], object_ids: dict[str, int],
                    geometry_paths: dict[str, Path],
                    name_for) -> list[str]:
    """Emit one ``<object>`` per distinct shape.

    Shapes are written once however many copies reference them, which is what
    keeps a repetitive set small.
    """
    out: list[str] = []
    for key in keys:
        mesh = read_stl(geometry_paths[key])
        vertices, triangles = _weld(mesh)
        if not triangles:
            continue
        name = saxutils.quoteattr(name_for(key))
        out.append(f'  <object id="{object_ids[key]}" type="model" name={name}>\n'
                   '   <mesh>\n    <vertices>\n')
        out.extend(
            f'     <vertex x="{_number(v[0])}" y="{_number(v[1])}" z="{_number(v[2])}"/>\n'
            for v in vertices)
        out.append('    </vertices>\n    <triangles>\n')
        out.extend(
            f'     <triangle v1="{t[0]}" v2="{t[1]}" v3="{t[2]}"/>\n' for t in triangles)
        out.append('    </triangles>\n   </mesh>\n  </object>\n')
    return out


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

    parts.extend(_mesh_resources(used_keys, object_ids, geometry_paths,
                                 lambda key: _object_name(plate, key)))

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
        stem = f"{stem}_{sanitize_filename(plate.label, max_length=24, fallback='')}"
    return f"{stem}.3mf"


class TooManyPlates(ValueError):
    """More plates than a single slicer project can hold."""


def _project_settings(bed_width: float, bed_depth: float, height: float = 250.0) -> str:
    """Declare the bed the plates were packed for.

    Without this the slicer lays its plate grid out using whatever printer
    profile happens to be selected. Pack for a 256 mm bed, open it on an
    A1 mini profile, and every plate after the first is offset by the
    difference — the parts drift further right with each plate.

    Only the keys that define the print area are written; everything else
    stays as the user's own profile.
    """
    area = [f"0x0", f"{_number(bed_width)}x0",
            f"{_number(bed_width)}x{_number(bed_depth)}", f"0x{_number(bed_depth)}"]
    return json.dumps({
        "printable_area": area,
        "printable_height": _number(height),
        "print_sequence": "by layer",
        "from": "project",
    }, indent=4)


def write_project_3mf(plates: list[Plate], geometry_paths: dict[str, Path],
                      destination: Path, *, title: str = "",
                      bed_height_mm: float = 250.0) -> int:
    """Write every plate into ONE project file, each plate a single object.

    Each plate becomes one 3MF object whose ``<components>`` reference the
    shared per-shape meshes. The slicer turns those components into parts of
    a single object, so a plate is one thing you can select and drag — while
    the geometry is still stored once per shape.

    The plate objects are then placed at their plate's world origin, because
    the slicer decides which plate an instance belongs to by where it sits
    (``PartPlateList::reload_all_objects``). The bed those origins were
    computed from is declared in ``project_settings.config`` so the grid
    matches whatever printer profile is loaded.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if len(plates) > MAX_PROJECT_PLATES:
        raise TooManyPlates(
            f"{len(plates)} plates exceeds the {MAX_PROJECT_PLATES}-plate "
            "limit of a single project file")

    # One mesh object per distinct shape across the whole project.
    used_keys: list[str] = []
    for plate in plates:
        for placement in plate.placements:
            key = placement.item.geometry_key
            if key not in used_keys and key in geometry_paths:
                used_keys.append(key)
    object_ids = {key: number for number, key in enumerate(used_keys, start=1)}

    names: dict[str, str] = {}
    for plate in plates:
        for placement in plate.placements:
            names.setdefault(placement.item.geometry_key,
                             f"{placement.item.part_num} {placement.item.name}"[:96])

    model: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}">\n',
        # These make the slicer open this as a project with real plates
        # rather than as a plain pile of geometry.
        f' <metadata name="Application">{BAMBU_GENERATOR}</metadata>\n',
        f' <metadata name="BambuStudio:3mfVersion">{BAMBU_3MF_VERSION}</metadata>\n',
        ' <metadata name="Description">Generated by Brick Foundry</metadata>\n',
    ]
    if title:
        model.append(f' <metadata name="Title">{saxutils.escape(title)}</metadata>\n')
    model.append(' <resources>\n')
    model.extend(_mesh_resources(used_keys, object_ids, geometry_paths,
                                 lambda key: names.get(key, key)))

    # A component object per plate. 3MF requires referenced objects to be
    # declared before the object referencing them, hence meshes first.
    plate_object_ids: list[int] = []
    next_id = len(used_keys) + 1
    for plate in plates:
        label = _plate_label(plate)
        model.append(f'  <object id="{next_id}" type="model" '
                     f'name={saxutils.quoteattr(label)}>\n   <components>\n')
        for placement in plate.placements:
            shape_id = object_ids.get(placement.item.geometry_key)
            if shape_id is None:
                continue
            transform = _transform(placement.x, placement.y, 0.0, placement.rotated)
            model.append(f'    <component objectid="{shape_id}" '
                         f'transform="{transform}"/>\n')
        model.append('   </components>\n  </object>\n')
        plate_object_ids.append(next_id)
        next_id += 1

    model.append(' </resources>\n <build>\n')

    columns = plate_columns(len(plates))
    bed_width = plates[0].bed_width if plates else 256.0
    bed_depth = plates[0].bed_depth if plates else 256.0

    for index, object_id in enumerate(plate_object_ids):
        offset_x, offset_y = plate_origin(index, columns, bed_width, bed_depth)
        transform = _transform(offset_x, offset_y, 0.0, False)
        model.append(f'  <item objectid="{object_id}" transform="{transform}" '
                     f'printable="1"/>\n')
    model.append(' </build>\n</model>\n')

    settings = _model_settings(plates, plate_object_ids)

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("3D/3dmodel.model", "".join(model))
        zf.writestr("Metadata/model_settings.config", settings)
        zf.writestr("Metadata/project_settings.config",
                    _project_settings(bed_width, bed_depth, bed_height_mm))

    return destination.stat().st_size


def _plate_label(plate: Plate) -> str:
    """What the plate is called in the slicer."""
    return plate.label


def _model_settings(plates: list[Plate], plate_object_ids: list[int]) -> str:
    """Bambu's ``model_settings.config``: object names and plate contents.

    Structure and attribute names follow the slicer's own exporter
    (``bbs_3mf.cpp``). With one object per plate there is exactly one
    instance on each plate, which is also what makes a plate draggable as a
    single unit.
    """
    out: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>\n', '<config>\n']

    for plate, object_id in zip(plates, plate_object_ids):
        label = _plate_label(plate)
        out.append(f'  <object id="{object_id}">\n')
        out.append(f'    <metadata key="name" value={saxutils.quoteattr(label)}/>\n')
        out.append('  </object>\n')

    for index, (plate, object_id) in enumerate(zip(plates, plate_object_ids)):
        out.append('  <plate>\n')
        out.append(f'    <metadata key="plater_id" value="{index + 1}"/>\n')
        out.append(f'    <metadata key="plater_name" '
                   f'value={saxutils.quoteattr(_plate_label(plate))}/>\n')
        out.append('    <metadata key="locked" value="false"/>\n')
        out.append('    <model_instance>\n')
        out.append(f'      <metadata key="object_id" value="{object_id}"/>\n')
        out.append('      <metadata key="instance_id" value="0"/>\n')
        out.append(f'      <metadata key="identify_id" value="{index + 1}"/>\n')
        out.append('    </model_instance>\n')
        out.append('  </plate>\n')

    out.append('</config>\n')
    return "".join(out)


def project_filename(set_number: str) -> str:
    from .normalize import sanitize_filename
    safe = sanitize_filename(set_number, max_length=24, fallback="set")
    return f"LEGO_{safe}_All_Plates.3mf"

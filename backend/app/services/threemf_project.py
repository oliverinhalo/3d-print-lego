"""Bambu-style project 3MF, mirroring the structure a real one uses.

The plain core-3MF project (``threemf_service.write_project_3mf``) is valid
and loads, but Bambu Studio has not been reproducing its plates. Rather than
keep guessing, this writer mirrors the layout of a known-good multi-plate
project file part for part:

    [Content_Types].xml
    _rels/.rels                     -> /3D/3dmodel.model
    3D/3dmodel.model                wrapper objects + build items
    3D/_rels/3dmodel.model.rels     -> every external mesh file
    3D/Objects/object_<n>.model     one mesh each
    Metadata/model_settings.config  plate names and contents
    Metadata/project_settings.config the bed it was packed for
    Metadata/slice_info.config

The differences from the core-only version, any of which could be what the
slicer keys on:

* the production extension is declared and required
  (``xmlns:p`` + ``requiredextensions="p"``);
* meshes live in separate part files, referenced by ``p:path``;
* every object, component and build item carries a ``p:UUID``;
* ``<resources>`` holds thin wrapper objects that reference the meshes,
  rather than the meshes themselves.

``group_per_plate`` selects between the two shapes worth testing: one wrapper
per plate (a plate is a single draggable object) or one wrapper per piece
(exactly what the reference file does).
"""
from __future__ import annotations

import json
import uuid
import xml.sax.saxutils as saxutils
import zipfile
from pathlib import Path

from ..ldraw.stl import read_stl
from .plate_service import Plate
from .threemf_service import (BAMBU_3MF_VERSION, BAMBU_GENERATOR, MAX_PROJECT_PLATES,
                              TooManyPlates, _number, _transform, _weld,
                              plate_columns, plate_origin)

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PRODUCTION_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
BAMBU_NS = "http://schemas.bambulab.com/package/2021"
MODEL_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Default Extension="gcode" ContentType="text/x.gcode"/>
</Types>
"""

ROOT_RELS = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="{MODEL_REL}"/>
</Relationships>
"""

SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value="02.01.01.52"/>
  </header>
</config>
"""

MODEL_HEADER = (
    '<model xmlns="{core}" xmlns:p="{prod}" unit="millimeter" xml:lang="en-US" '
    'requiredextensions="p" xmlns:BambuStudio="{bambu}">\n'
).format(core=CORE_NS, prod=PRODUCTION_NS, bambu=BAMBU_NS)


def _uuid() -> str:
    return str(uuid.uuid4())


def _mesh_part(object_id: int, name: str, stl_path: Path) -> str:
    """One external ``3D/Objects/object_N.model`` holding a single mesh."""
    mesh = read_stl(stl_path)
    vertices, triangles = _weld(mesh)
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n', MODEL_HEADER,
           f' <metadata name="BambuStudio:3mfVersion">{BAMBU_3MF_VERSION}</metadata>\n',
           ' <resources>\n',
           f'  <object id="{object_id}" p:UUID="{_uuid()}" type="model" '
           f'name={saxutils.quoteattr(name)}>\n   <mesh>\n    <vertices>\n']
    out.extend(f'     <vertex x="{_number(v[0])}" y="{_number(v[1])}" z="{_number(v[2])}"/>\n'
               for v in vertices)
    out.append('    </vertices>\n    <triangles>\n')
    out.extend(f'     <triangle v1="{t[0]}" v2="{t[1]}" v3="{t[2]}"/>\n' for t in triangles)
    out.append('    </triangles>\n   </mesh>\n  </object>\n </resources>\n <build/>\n</model>\n')
    return "".join(out)


def _project_settings(bed_width: float, bed_depth: float, height: float) -> str:
    area = ["0x0", f"{_number(bed_width)}x0",
            f"{_number(bed_width)}x{_number(bed_depth)}", f"0x{_number(bed_depth)}"]
    return json.dumps({
        "printable_area": area,
        "printable_height": _number(height),
        "print_sequence": "by layer",
        "from": "project",
        "version": "02.01.01.52",
    }, indent=4)


def write_bambu_project(plates: list[Plate], geometry_paths: dict[str, Path],
                        destination: Path, *, title: str = "",
                        bed_height_mm: float = 250.0,
                        group_per_plate: bool = True) -> int:
    """Write a Bambu-style multi-plate project.

    ``group_per_plate`` puts every piece on a plate into one wrapper object,
    so the plate moves as a unit. Setting it False emits one wrapper per
    piece, which is exactly the shape of a slicer-authored file.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if len(plates) > MAX_PROJECT_PLATES:
        raise TooManyPlates(
            f"{len(plates)} plates exceeds the {MAX_PROJECT_PLATES}-plate limit")

    # --- external mesh parts, one file per distinct shape -------------------
    shape_keys: list[str] = []
    for plate in plates:
        for placement in plate.placements:
            key = placement.item.geometry_key
            if key not in shape_keys and key in geometry_paths:
                shape_keys.append(key)

    shape_names: dict[str, str] = {}
    for plate in plates:
        for placement in plate.placements:
            shape_names.setdefault(placement.item.geometry_key,
                                   f"{placement.item.part_num} {placement.item.name}"[:96])

    mesh_files: dict[str, tuple[str, int]] = {}      # key -> (path, object id)
    parts_xml: dict[str, str] = {}
    for number, key in enumerate(shape_keys, start=1):
        path = f"/3D/Objects/object_{number}.model"
        mesh_files[key] = (path, number)
        parts_xml[path] = _mesh_part(number, shape_names.get(key, key),
                                     geometry_paths[key])

    # --- the main model: wrappers + build items ----------------------------
    columns = plate_columns(len(plates))
    bed_width = plates[0].bed_width if plates else 256.0
    bed_depth = plates[0].bed_depth if plates else 256.0

    model: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>\n', MODEL_HEADER,
                        f' <metadata name="Application">{BAMBU_GENERATOR}</metadata>\n',
                        f' <metadata name="BambuStudio:3mfVersion">{BAMBU_3MF_VERSION}</metadata>\n',
                        ' <metadata name="Description">Generated by Brick Foundry</metadata>\n']
    if title:
        model.append(f' <metadata name="Title">{saxutils.escape(title)}</metadata>\n')
    model.append(' <resources>\n')

    # Wrapper ids continue after the mesh ids so nothing collides.
    next_id = len(shape_keys) + 1
    build: list[tuple[int, float, float, bool]] = []   # id, x, y, rotated
    plate_members: list[list[int]] = []

    for index, plate in enumerate(plates):
        origin_x, origin_y = plate_origin(index, columns, bed_width, bed_depth)
        members: list[int] = []

        if group_per_plate:
            model.append(f'  <object id="{next_id}" p:UUID="{_uuid()}" type="model" '
                         f'name={saxutils.quoteattr(plate.label)}>\n   <components>\n')
            for placement in plate.placements:
                entry = mesh_files.get(placement.item.geometry_key)
                if entry is None:
                    continue
                path, mesh_id = entry
                transform = _transform(placement.x, placement.y, 0.0, placement.rotated)
                model.append(f'    <component p:path="{path}" objectid="{mesh_id}" '
                             f'p:UUID="{_uuid()}" transform="{transform}"/>\n')
            model.append('   </components>\n  </object>\n')
            build.append((next_id, origin_x, origin_y, False))
            members.append(next_id)
            next_id += 1
        else:
            for placement in plate.placements:
                entry = mesh_files.get(placement.item.geometry_key)
                if entry is None:
                    continue
                path, mesh_id = entry
                name = f"{placement.item.part_num} {placement.item.name}"[:96]
                model.append(f'  <object id="{next_id}" p:UUID="{_uuid()}" type="model" '
                             f'name={saxutils.quoteattr(name)}>\n   <components>\n'
                             f'    <component p:path="{path}" objectid="{mesh_id}" '
                             f'p:UUID="{_uuid()}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n'
                             '   </components>\n  </object>\n')
                build.append((next_id, origin_x + placement.x, origin_y + placement.y,
                              placement.rotated))
                members.append(next_id)
                next_id += 1

        plate_members.append(members)

    model.append(' </resources>\n <build p:UUID="' + _uuid() + '">\n')
    for object_id, x, y, rotated in build:
        model.append(f'  <item objectid="{object_id}" p:UUID="{_uuid()}" '
                     f'transform="{_transform(x, y, 0.0, rotated)}" printable="1"/>\n')
    model.append(' </build>\n</model>\n')

    # --- relationships from the main model to every mesh part --------------
    rels = ['<?xml version="1.0" encoding="UTF-8"?>\n',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n']
    for number, path in enumerate(parts_xml, start=1):
        rels.append(f' <Relationship Target="{path}" Id="rel-{number}" '
                    f'Type="{MODEL_REL}"/>\n')
    rels.append('</Relationships>\n')

    settings = _model_settings(plates, plate_members)

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", ROOT_RELS)
        zf.writestr("3D/3dmodel.model", "".join(model))
        zf.writestr("3D/_rels/3dmodel.model.rels", "".join(rels))
        for path, xml in parts_xml.items():
            zf.writestr(path.lstrip("/"), xml)
        zf.writestr("Metadata/model_settings.config", settings)
        zf.writestr("Metadata/project_settings.config",
                    _project_settings(bed_width, bed_depth, bed_height_mm))
        zf.writestr("Metadata/slice_info.config", SLICE_INFO)

    return destination.stat().st_size


def _model_settings(plates: list[Plate], plate_members: list[list[int]]) -> str:
    """Plate names and which objects sit on them."""
    out: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>\n', '<config>\n']

    for plate, members in zip(plates, plate_members):
        for object_id in members:
            out.append(f'  <object id="{object_id}">\n')
            out.append(f'    <metadata key="name" '
                       f'value={saxutils.quoteattr(plate.label)}/>\n')
            out.append('  </object>\n')

    identify = 1
    for index, (plate, members) in enumerate(zip(plates, plate_members)):
        out.append('  <plate>\n')
        out.append(f'    <metadata key="plater_id" value="{index + 1}"/>\n')
        out.append(f'    <metadata key="plater_name" '
                   f'value={saxutils.quoteattr(plate.label)}/>\n')
        out.append('    <metadata key="locked" value="false"/>\n')
        for object_id in members:
            out.append('    <model_instance>\n')
            out.append(f'      <metadata key="object_id" value="{object_id}"/>\n')
            out.append('      <metadata key="instance_id" value="0"/>\n')
            out.append(f'      <metadata key="identify_id" value="{identify}"/>\n')
            out.append('    </model_instance>\n')
            identify += 1
        out.append('  </plate>\n')

    out.append('</config>\n')
    return "".join(out)

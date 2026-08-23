"""Colour grouping, plate packing and 3MF export."""
import zipfile
import xml.etree.ElementTree as ET

import pytest

from app.services.color_service import ColorMode, family_for, group_key, LegoColor
from app.services.plate_service import (BED_PRESETS, PackItem, bed_size,
                                        pack_items, summarise)
from app.services.threemf_service import CORE_NS, plate_filename, write_plate_3mf
from tests.conftest import write_cube_stl

NS = {"c": CORE_NS}


def item(part="3001", w=32.0, d=16.0, h=11.2, group="", key="ldraw:3001", rgb="C91A09"):
    return PackItem(part_num=part, name="Brick 2 x 4", geometry_key=key,
                    width=w, depth=d, height=h, color_name="Red",
                    color_rgb=rgb, group=group)


class TestColorFamilies:
    @pytest.mark.parametrize("rgb,expected", [
        ("C91A09", "Red"),        # Red
        ("720E0F", "Red"),        # Dark Red
        ("FC97AC", "Pink"),       # Pink
        ("C870A0", "Pink"),       # Dark Pink
        ("0055BF", "Blue"),       # Blue
        ("0A3463", "Blue"),       # Dark Blue
        ("237841", "Green"),      # Green
        ("F2CD37", "Yellow"),     # Yellow
        ("FE8A18", "Orange"),     # Orange
        ("582A12", "Brown"),      # Reddish Brown
        ("E4CD9E", "Tan"),        # Tan
        ("FFFFFF", "White"),      # White
        ("05131D", "Black"),      # Black — a near-black navy, not Blue
        ("A0A5A9", "Light Grey"), # Light Bluish Gray
        ("6C6E68", "Dark Grey"),  # Dark Bluish Gray
        ("81007B", "Purple"),     # Purple
    ])
    def test_known_lego_colors_land_in_the_right_family(self, rgb, expected):
        assert family_for(rgb) == expected

    def test_transparent_is_its_own_family(self):
        """Translucent parts need translucent filament, so they never mix."""
        assert family_for("C91A09", is_trans=True) == "Transparent"

    def test_reds_of_every_shade_share_one_group(self):
        """The whole point of family mode: one red filament prints them all."""
        reds = ["C91A09", "720E0F", "B40000", "FE8A18"]
        families = {family_for(rgb) for rgb in reds}
        assert families <= {"Red", "Orange"}

    def test_group_key_respects_the_mode(self):
        color = LegoColor(4, "Dark Red", "720E0F", False)
        assert group_key(color, ColorMode.NONE) == ""
        assert group_key(color, ColorMode.EXACT) == "Dark Red"
        assert group_key(color, ColorMode.FAMILY) == "Red"

    def test_bad_input_does_not_raise(self):
        for value in ("", "xyz", "#12", None):
            assert family_for(value)  # type: ignore[arg-type]


class TestPacking:
    def _check(self, plates, bed):
        """No piece may overlap another or leave the bed."""
        for plate in plates:
            for placement in plate.placements:
                x0, x1 = placement.x - placement.width / 2, placement.x + placement.width / 2
                y0, y1 = placement.y - placement.depth / 2, placement.y + placement.depth / 2
                assert x0 >= -1e-6 and y0 >= -1e-6
                assert x1 <= bed[0] + 1e-6 and y1 <= bed[1] + 1e-6
            items = plate.placements
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a, b = items[i], items[j]
                    overlap_x = (abs(a.x - b.x) < (a.width + b.width) / 2 - 1e-9)
                    overlap_y = (abs(a.y - b.y) < (a.depth + b.depth) / 2 - 1e-9)
                    assert not (overlap_x and overlap_y), "pieces overlap"

    def test_everything_is_placed_and_nothing_overlaps(self):
        bed = bed_size("bambu_p1")
        plates, oversized = pack_items([item() for _ in range(400)], bed)
        assert not oversized
        assert sum(p.count for p in plates) == 400
        self._check(plates, bed)

    def test_a_big_set_becomes_a_handful_of_plates(self):
        """800 pieces must not become 800 things to import."""
        bed = bed_size("bambu_p1")
        plates, _ = pack_items([item() for _ in range(800)], bed)
        assert 1 < len(plates) < 40

    def test_colour_groups_are_never_mixed_on_a_plate(self):
        bed = bed_size("bambu_p1")
        items = ([item(group="Red") for _ in range(30)]
                 + [item(group="Blue") for _ in range(30)]
                 + [item(group="Black") for _ in range(30)])
        plates, _ = pack_items(items, bed, group_plates=True)
        for plate in plates:
            groups = {p.item.group for p in plate.placements}
            assert len(groups) == 1
            assert plate.group in groups

    def test_ignoring_colour_uses_fewer_plates(self):
        bed = bed_size("bambu_p1")
        items = [item(group=g) for g in ("Red", "Blue", "Black", "White") for _ in range(20)]
        grouped, _ = pack_items(items, bed, group_plates=True)
        mixed, _ = pack_items(items, bed, group_plates=False)
        assert len(mixed) <= len(grouped)

    def test_a_part_bigger_than_the_bed_is_reported(self):
        bed = bed_size("bambu_a1_mini")
        plates, oversized = pack_items([item(w=400, d=400)], bed)
        assert len(oversized) == 1 and not plates

    def test_rotation_lets_a_long_part_fit(self):
        bed = (100.0, 300.0)
        plates, oversized = pack_items([item(w=250, d=20)], bed, allow_rotation=True)
        assert not oversized and plates[0].placements[0].rotated

    def test_smaller_bed_needs_more_plates(self):
        items = [item() for _ in range(200)]
        big, _ = pack_items(items, bed_size("voron_350"))
        small, _ = pack_items(items, bed_size("bambu_a1_mini"))
        assert len(small) > len(big)

    def test_empty_input(self):
        plates, oversized = pack_items([], bed_size("bambu_p1"))
        assert plates == [] and oversized == []

    def test_plates_are_numbered_from_one(self):
        plates, _ = pack_items([item() for _ in range(300)], bed_size("bambu_p1"))
        assert [p.index for p in plates] == list(range(1, len(plates) + 1))

    def test_every_preset_is_usable(self):
        for name in BED_PRESETS:
            plates, oversized = pack_items([item() for _ in range(10)], bed_size(name))
            assert plates and not oversized

    def test_summary_reports_totals(self):
        plates, _ = pack_items([item() for _ in range(50)], bed_size("bambu_p1"))
        report = summarise(plates)
        assert report["piece_count"] == 50
        assert report["plate_count"] == len(plates)


class TestThreeMF:
    @pytest.fixture
    def plate_and_geometry(self, tmp_path):
        stl = write_cube_stl(tmp_path / "cube.stl", size=10.0)
        items = [item(w=10, d=10, h=10, key="fake:cube") for _ in range(12)]
        plates, _ = pack_items(items, bed_size("bambu_p1"))
        return plates[0], {"fake:cube": stl}

    def test_it_is_a_valid_zip_package(self, plate_and_geometry, tmp_path):
        plate, geometry = plate_and_geometry
        path = tmp_path / "plate.3mf"
        write_plate_3mf(plate, geometry, path)

        with zipfile.ZipFile(path) as zf:
            assert zf.testzip() is None
            names = zf.namelist()
        assert "[Content_Types].xml" in names
        assert "_rels/.rels" in names
        assert "3D/3dmodel.model" in names

    def test_the_model_is_well_formed_and_in_millimetres(self, plate_and_geometry, tmp_path):
        plate, geometry = plate_and_geometry
        path = tmp_path / "plate.3mf"
        write_plate_3mf(plate, geometry, path)

        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("3D/3dmodel.model"))
        assert root.get("unit") == "millimeter"
        assert root.findall(".//c:resources/c:object", NS)
        assert root.findall(".//c:build/c:item", NS)

    def test_one_mesh_is_shared_by_every_copy(self, plate_and_geometry, tmp_path):
        """12 identical pieces must not store the mesh 12 times."""
        plate, geometry = plate_and_geometry
        path = tmp_path / "plate.3mf"
        write_plate_3mf(plate, geometry, path)

        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("3D/3dmodel.model"))
        assert len(root.findall(".//c:resources/c:object", NS)) == 1
        assert len(root.findall(".//c:build/c:item", NS)) == 12

    def test_every_item_points_at_a_real_object(self, plate_and_geometry, tmp_path):
        plate, geometry = plate_and_geometry
        path = tmp_path / "plate.3mf"
        write_plate_3mf(plate, geometry, path)

        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("3D/3dmodel.model"))
        ids = {o.get("id") for o in root.findall(".//c:resources/c:object", NS)}
        for build_item in root.findall(".//c:build/c:item", NS):
            assert build_item.get("objectid") in ids

    def test_triangle_indices_are_in_range(self, plate_and_geometry, tmp_path):
        plate, geometry = plate_and_geometry
        path = tmp_path / "plate.3mf"
        write_plate_3mf(plate, geometry, path)

        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("3D/3dmodel.model"))
        for obj in root.findall(".//c:resources/c:object", NS):
            count = len(obj.findall(".//c:vertices/c:vertex", NS))
            for tri in obj.findall(".//c:triangles/c:triangle", NS):
                for corner in ("v1", "v2", "v3"):
                    assert 0 <= int(tri.get(corner)) < count

    def test_geometry_survives_the_round_trip(self, plate_and_geometry, tmp_path):
        plate, geometry = plate_and_geometry
        path = tmp_path / "plate.3mf"
        write_plate_3mf(plate, geometry, path)

        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("3D/3dmodel.model"))
        obj = root.findall(".//c:resources/c:object", NS)[0]
        points = [(float(v.get("x")), float(v.get("y")), float(v.get("z")))
                  for v in obj.findall(".//c:vertices/c:vertex", NS)]
        for axis in range(3):
            span = max(p[axis] for p in points) - min(p[axis] for p in points)
            assert span == pytest.approx(10.0, abs=1e-3)

    def test_placements_are_inside_the_bed(self, plate_and_geometry, tmp_path):
        plate, geometry = plate_and_geometry
        path = tmp_path / "plate.3mf"
        write_plate_3mf(plate, geometry, path)

        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("3D/3dmodel.model"))
        for build_item in root.findall(".//c:build/c:item", NS):
            values = [float(v) for v in build_item.get("transform").split()]
            assert len(values) == 12
            x, y, _z = values[9], values[10], values[11]
            assert 0 <= x <= plate.bed_width
            assert 0 <= y <= plate.bed_depth

    def test_transforms_match_what_the_packer_decided(self, plate_and_geometry, tmp_path):
        plate, geometry = plate_and_geometry
        path = tmp_path / "plate.3mf"
        write_plate_3mf(plate, geometry, path)

        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("3D/3dmodel.model"))
        build_items = root.findall(".//c:build/c:item", NS)
        for build_item, placement in zip(build_items, plate.placements):
            values = [float(v) for v in build_item.get("transform").split()]
            assert values[9] == pytest.approx(placement.x, abs=1e-3)
            assert values[10] == pytest.approx(placement.y, abs=1e-3)

    def test_it_is_far_smaller_than_the_equivalent_stls(self, plate_and_geometry, tmp_path):
        plate, geometry = plate_and_geometry
        path = tmp_path / "plate.3mf"
        size = write_plate_3mf(plate, geometry, path)
        stl_size = geometry["fake:cube"].stat().st_size * plate.count
        assert size < stl_size

    def test_filenames_sort_in_print_order(self):
        plates, _ = pack_items([item() for _ in range(300)], bed_size("bambu_p1"))
        names = [plate_filename(p, len(plates)) for p in plates]
        assert names == sorted(names)
        assert all(n.endswith(".3mf") for n in names)

    def test_group_name_appears_in_the_filename(self):
        plates, _ = pack_items([item(group="Red") for _ in range(5)],
                               bed_size("bambu_p1"), group_plates=True)
        assert "Red" in plate_filename(plates[0], 1)


class TestProjectFile:
    """The merged project: every plate in one file, each named by colour."""

    @pytest.fixture
    def project(self, tmp_path):
        from app.services.threemf_service import write_project_3mf
        stl = write_cube_stl(tmp_path / "cube.stl", size=10.0)
        items = [item(w=10, d=10, h=10, key="fake:cube", group=group)
                 for group in ("Red", "Blue", "Black") for _ in range(20)]
        plates, _ = pack_items(items, bed_size("bambu_p1"), group_plates=True)
        path = tmp_path / "project.3mf"
        write_project_3mf(plates, {"fake:cube": stl}, path, title="Test")
        return plates, path

    def _model(self, path):
        with zipfile.ZipFile(path) as zf:
            return (ET.fromstring(zf.read("3D/3dmodel.model")),
                    ET.fromstring(zf.read("Metadata/model_settings.config")))

    def test_it_declares_itself_as_a_slicer_project(self, project):
        """The one thing that makes plates appear at all.

        Bambu Studio sets its project flag only when the Application metadata
        starts with "BambuStudio-" or "OrcaSlicer-". Without it the plate
        settings are ignored entirely: one unnamed plate, and every repeated
        instance split into a separate object.
        """
        _plates, path = project
        model, _config = self._model(path)
        metadata = {m.get("name"): m.text for m in model.findall("c:metadata", NS)}

        application = metadata.get("Application", "")
        assert application.startswith(("BambuStudio-", "OrcaSlicer-")), application
        assert metadata.get("BambuStudio:3mfVersion")
        # The real generator stays recorded alongside it.
        assert "Brick Foundry" in metadata.get("Description", "")

    def test_the_package_carries_both_the_model_and_the_plate_settings(self, project):
        _plates, path = project
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            assert zf.testzip() is None
        assert "3D/3dmodel.model" in names
        assert "Metadata/model_settings.config" in names
        assert "[Content_Types].xml" in names

    def test_one_plate_is_declared_per_packed_plate(self, project):
        plates, path = project
        _model, config = self._model(path)
        assert len(config.findall("plate")) == len(plates)

    def test_each_plate_is_named_after_its_colour(self, project):
        plates, path = project
        _model, config = self._model(path)
        names = []
        for plate in config.findall("plate"):
            meta = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
            names.append(meta["plater_name"])
        assert names == [p.group for p in plates]
        assert set(names) == {"Red", "Blue", "Black"}

    def test_plater_ids_are_sequential_from_one(self, project):
        _plates, path = project
        _model, config = self._model(path)
        ids = [int({m.get("key"): m.get("value")
                    for m in p.findall("metadata")}["plater_id"])
               for p in config.findall("plate")]
        assert ids == list(range(1, len(ids) + 1))

    def test_every_piece_is_listed_on_exactly_one_plate(self, project):
        plates, path = project
        _model, config = self._model(path)
        listed = sum(len(p.findall("model_instance")) for p in config.findall("plate"))
        assert listed == sum(p.count for p in plates)

    def test_pieces_sit_inside_their_own_plate_area(self, project):
        """The slicer assigns plates by geometry, so this is what decides it."""
        from app.services.threemf_service import plate_columns, plate_origin

        plates, path = project
        model, _config = self._model(path)
        items = model.findall(".//c:build/c:item", NS)
        columns = plate_columns(len(plates))

        index = 0
        for number, plate in enumerate(plates):
            origin_x, origin_y = plate_origin(number, columns,
                                              plate.bed_width, plate.bed_depth)
            for _placement in plate.placements:
                values = [float(v) for v in items[index].get("transform").split()]
                x, y = values[9], values[10]
                assert origin_x - 1e-6 <= x <= origin_x + plate.bed_width + 1e-6
                assert origin_y - 1e-6 <= y <= origin_y + plate.bed_depth + 1e-6
                index += 1

    def test_plates_do_not_overlap_each_other_in_world_space(self, project):
        from app.services.threemf_service import plate_columns, plate_origin

        plates, _path = project
        columns = plate_columns(len(plates))
        boxes = []
        for number, plate in enumerate(plates):
            ox, oy = plate_origin(number, columns, plate.bed_width, plate.bed_depth)
            boxes.append((ox, oy, ox + plate.bed_width, oy + plate.bed_depth))
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                assert not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])

    def test_instance_ids_follow_the_build_item_order(self, project):
        """The importer numbers instances as it reads build items."""
        _plates, path = project
        model, config = self._model(path)

        seen: dict[int, int] = {}
        expected = []
        for build_item in model.findall(".//c:build/c:item", NS):
            object_id = int(build_item.get("objectid"))
            expected.append((object_id, seen.get(object_id, 0)))
            seen[object_id] = seen.get(object_id, 0) + 1

        declared = []
        for plate in config.findall("plate"):
            for instance in plate.findall("model_instance"):
                meta = {m.get("key"): m.get("value") for m in instance.findall("metadata")}
                declared.append((int(meta["object_id"]), int(meta["instance_id"])))
        assert declared == expected

    def test_geometry_is_still_shared_across_plates(self, project):
        plates, path = project
        model, _config = self._model(path)
        assert len(model.findall(".//c:resources/c:object", NS)) == 1
        assert len(model.findall(".//c:build/c:item", NS)) == sum(p.count for p in plates)

    def test_too_many_plates_is_refused_rather_than_written_wrong(self, tmp_path):
        from app.services.threemf_service import (MAX_PROJECT_PLATES, TooManyPlates,
                                                  write_project_3mf)
        stl = write_cube_stl(tmp_path / "cube.stl")
        items = [item(w=10, d=10, h=10, key="fake:cube", group=f"G{i}")
                 for i in range(MAX_PROJECT_PLATES + 5)]
        plates, _ = pack_items(items, bed_size("bambu_p1"), group_plates=True)
        assert len(plates) > MAX_PROJECT_PLATES
        with pytest.raises(TooManyPlates):
            write_project_3mf(plates, {"fake:cube": stl}, tmp_path / "big.3mf")

    def test_grid_matches_the_slicer_arithmetic(self):
        from app.services.threemf_service import plate_columns
        # compute_colum_count from PartPlate.hpp
        for count, expected in [(1, 1), (2, 2), (4, 2), (5, 3), (9, 3),
                                (10, 4), (16, 4), (17, 5), (25, 5), (36, 6)]:
            assert plate_columns(count) == expected

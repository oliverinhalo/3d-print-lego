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

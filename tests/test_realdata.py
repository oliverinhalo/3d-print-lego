"""Tests against the real LDraw library and Rebrickable catalogue.

These skip automatically when the data has not been downloaded, so the suite
still runs on a clean checkout.
"""
import pytest

from app.ldraw.convert import convert_part, ldraw_to_print_space, orient_for_printing
from app.ldraw.parser import LDrawError

pytestmark = pytest.mark.realdata


#: Real LEGO elements and their true LDraw dimensions in millimetres.
#: LDraw models nominal sizes, so a 2x4 brick is 32.00 mm, not the 31.80 mm
#: of a moulded brick; the stud adds 1.6 mm to the height.
KNOWN_DIMENSIONS = {
    "3001": (32.0, 16.0, 11.2),    # Brick 2 x 4
    "3003": (16.0, 16.0, 11.2),    # Brick 2 x 2
    "3005": (8.0, 8.0, 11.2),      # Brick 1 x 1
    "3024": (8.0, 8.0, 4.8),       # Plate 1 x 1
    "3023": (16.0, 8.0, 4.8),      # Plate 1 x 2
    "3070b": (8.0, 8.0, 3.2),      # Tile 1 x 1 with groove
    "3673": (16.0, 6.4, 6.4),      # Technic pin
}


class TestGeometryIsDimensionallyCorrect:
    @pytest.mark.parametrize("part_id,expected", KNOWN_DIMENSIONS.items())
    def test_known_parts_measure_correctly(self, ldraw_library, part_id, expected):
        result = convert_part(ldraw_library, part_id)
        assert result.ok, result.validation.errors
        assert result.validation.dimensions == pytest.approx(expected, abs=0.01)

    def test_a_brick_is_not_scaled_to_metres_or_inches(self, ldraw_library):
        """Guards the unit conversion the spec warns about."""
        dims = convert_part(ldraw_library, "3001").validation.dimensions
        assert 10 < max(dims) < 100

    def test_parts_rest_on_the_build_plate(self, ldraw_library):
        for part_id in KNOWN_DIMENSIONS:
            mesh = convert_part(ldraw_library, part_id).mesh
            low, _high = mesh.bounds()
            assert low[2] == pytest.approx(0.0, abs=1e-6), f"{part_id} floats or sinks"

    def test_native_orientation_keeps_studs_up(self, ldraw_library):
        """A 1x1 brick must stay upright, not be tipped onto its side."""
        dims = convert_part(ldraw_library, "3005").validation.dimensions
        assert dims == pytest.approx((8.0, 8.0, 11.2), abs=0.01)

    def test_surfaces_face_outwards(self, ldraw_library):
        """A positive signed volume means the winding is right side out."""
        mesh = convert_part(ldraw_library, "3001").mesh
        volume = sum(
            (a[0] * (b[1] * c[2] - b[2] * c[1])
             - a[1] * (b[0] * c[2] - b[2] * c[0])
             + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
            for a, b, c in mesh.triangles)
        assert volume > 0, "inverted normals"
        assert 1000 < volume < 5000, "implausible volume for a 2x4 brick"

    def test_the_part_scale_setting_is_applied(self, ldraw_library):
        scaled = convert_part(ldraw_library, "3001", scale=0.994)
        assert scaled.validation.dimensions[0] == pytest.approx(31.808, abs=0.01)


class TestParser:
    def test_a_missing_part_raises_rather_than_producing_junk(self, ldraw_library):
        with pytest.raises(LDrawError):
            ldraw_library.load_part("definitely_not_a_part_9999")

    def test_subfiles_are_resolved(self, ldraw_library):
        """3001 is built from a subpart, so a naive parser yields too little."""
        mesh, stats = ldraw_library.load_part("3001")
        assert stats.subfiles > 0
        assert len(mesh) > 100

    def test_the_library_is_indexed(self, ldraw_library):
        assert ldraw_library.part_count() > 10_000
        assert ldraw_library.has_part("3001")
        assert not ldraw_library.has_part("nonexistent_part_xyz")

    def test_part_titles_are_available(self, ldraw_library):
        assert "2 x  4" in (ldraw_library.part_title("3001") or "")

    def test_conversion_is_cached_between_calls(self, ldraw_library):
        """Primitives are shared, so a second part must not reparse them."""
        import time
        ldraw_library.clear_cache()
        start = time.perf_counter()
        ldraw_library.load_part("3001")
        first = time.perf_counter() - start

        start = time.perf_counter()
        ldraw_library.load_part("3001")
        second = time.perf_counter() - start
        assert second < first


class TestCoordinateConversion:
    def test_ldraw_y_down_becomes_z_up(self):
        from app.ldraw.mesh import Mesh
        # One LDraw triangle one LDU "above" the origin (LDraw -Y is up).
        mesh = Mesh([((0, -1, 0), (1, -1, 0), (0, -1, 1))])
        converted = ldraw_to_print_space(mesh, ldu_mm=0.4)
        assert all(v[2] == pytest.approx(0.4) for v in converted.triangles[0])

    def test_orientation_only_moves_the_part(self):
        from app.ldraw.mesh import Mesh
        mesh = Mesh([((0, 0, 5), (10, 0, 5), (0, 10, 5))])
        placed = orient_for_printing(mesh)
        assert placed.dimensions() == pytest.approx(mesh.dimensions())


class TestCatalogue:
    @pytest.fixture
    def csv_provider(self, real_settings):
        from app.db import Database
        from app.providers.rebrickable_csv import RebrickableCSVProvider
        provider = RebrickableCSVProvider(Database(real_settings.sqlite_path))
        if not provider.available:
            pytest.skip("catalogue not imported (run scripts/bootstrap_data.py)")
        return provider

    def test_a_real_set_resolves(self, csv_provider):
        lego_set = csv_provider.get_set("77263-1")
        assert lego_set.name and lego_set.num_parts > 0

    def test_a_real_inventory_loads(self, csv_provider):
        inventory = csv_provider.get_inventory(csv_provider.get_set("77263-1"))
        assert len(inventory.entries) > 50
        assert inventory.total_pieces > 100

    def test_a_printed_part_resolves_to_its_plain_shape(self, csv_provider):
        """The geometry/appearance split, against real catalogue data."""
        candidates = dict(csv_provider.geometry_candidates("3070bpr0056"))
        assert "3070b" in candidates

    def test_stickers_are_not_treated_as_printable(self, csv_provider):
        assert not csv_provider.is_printable_part("10118464", "Sticker Sheet for Set 77263-1")
        assert csv_provider.is_printable_part("3001", "Brick 2 x 4")

    def test_coverage_of_a_real_set_is_high(self, csv_provider, ldraw_library):
        """The end-to-end feasibility claim, asserted rather than assumed."""
        from app.providers.ldraw_model import LDrawModelProvider

        lego_set = csv_provider.get_set("77263-1")
        inventory = csv_provider.get_inventory(lego_set)
        model_provider = LDrawModelProvider(
            type("S", (), {"ldraw_dir": ldraw_library.root, "ldu_mm": 0.4,
                           "part_scale": 1.0, "auto_orient": True,
                           "orient_strategy": "native", "stl_binary": True,
                           "source_directory": ldraw_library.root.parent})())

        pieces = matched = 0
        for entry in inventory.entries:
            if not csv_provider.is_printable_part(entry.part_num, entry.name):
                continue
            pieces += entry.quantity
            if any(model_provider.has_model(candidate)
                   for candidate, _ in csv_provider.geometry_candidates(entry.part_num)):
                matched += entry.quantity
        assert matched / pieces > 0.90, f"only {matched}/{pieces} pieces matched"

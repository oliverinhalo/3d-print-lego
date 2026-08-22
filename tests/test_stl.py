"""STL writing, reading and validation (spec section 14)."""
import struct
from pathlib import Path

import pytest

from app.ldraw.mesh import Mesh
from app.ldraw.stl import (read_stl, validate_mesh, validate_stl_file,
                           write_ascii_stl, write_binary_stl)
from tests.conftest import corrupt_stl, make_cube_mesh


class TestRoundTrip:
    def test_binary_round_trip_preserves_geometry(self, tmp_path):
        mesh = make_cube_mesh(10.0)
        path = tmp_path / "cube.stl"
        write_binary_stl(mesh, path)
        assert len(read_stl(path)) == len(mesh)
        assert read_stl(path).dimensions() == pytest.approx((10, 10, 10))

    def test_ascii_round_trip_preserves_geometry(self, tmp_path):
        path = tmp_path / "cube_ascii.stl"
        write_ascii_stl(make_cube_mesh(8.0), path)
        assert read_stl(path).dimensions() == pytest.approx((8, 8, 8), abs=1e-4)

    def test_binary_is_smaller_than_ascii(self, tmp_path):
        mesh = make_cube_mesh()
        binary = write_binary_stl(mesh, tmp_path / "b.stl")
        ascii_size = write_ascii_stl(mesh, tmp_path / "a.stl")
        assert binary < ascii_size


class TestValidation:
    def test_a_good_mesh_passes(self):
        assert validate_mesh(make_cube_mesh(10.0)).ok

    def test_empty_mesh_is_rejected(self):
        result = validate_mesh(Mesh([]))
        assert not result.ok and "no triangles" in result.errors[0]

    def test_nan_coordinates_are_rejected(self):
        bad = Mesh([((0, 0, 0), (float("nan"), 0, 0), (0, 1, 0))])
        result = validate_mesh(bad)
        assert not result.ok
        assert any("NaN" in e for e in result.errors)

    def test_infinite_coordinates_are_rejected(self):
        bad = Mesh([((0, 0, 0), (float("inf"), 0, 0), (0, 1, 0))])
        assert not validate_mesh(bad).ok

    def test_a_model_scaled_to_metres_is_caught(self):
        """The spec's example: 0.003 x 0.001 mm means a unit error."""
        tiny = make_cube_mesh(1.0).scaled(0.001)
        result = validate_mesh(tiny)
        assert not result.ok
        assert any("implausibly small" in e for e in result.errors)

    def test_an_absurdly_large_model_is_caught(self):
        result = validate_mesh(make_cube_mesh(1.0).scaled(5000))
        assert not result.ok
        assert any("implausibly large" in e for e in result.errors)

    def test_degenerate_triangles_are_reported(self):
        mesh = make_cube_mesh(10.0)
        mesh.triangles.append(((0, 0, 0), (0, 0, 0), (0, 0, 0)))
        result = validate_mesh(mesh)
        assert any("degenerate" in w for w in result.warnings)

    def test_closed_mesh_reports_no_boundary_edges(self):
        boundary, non_manifold = make_cube_mesh().edge_manifold_report()
        assert boundary == 0 and non_manifold == 0

    def test_open_mesh_is_detected(self):
        open_mesh = Mesh(make_cube_mesh().triangles[:-1])
        boundary, _ = open_mesh.edge_manifold_report()
        assert boundary > 0

    def test_manifold_requirement_can_be_enforced(self):
        open_mesh = Mesh(make_cube_mesh().triangles[:-2])
        assert validate_mesh(open_mesh, require_manifold=False).ok
        assert not validate_mesh(open_mesh, require_manifold=True).ok


class TestFileValidation:
    def test_valid_file_passes(self, cube_stl):
        result = validate_stl_file(cube_stl)
        assert result.ok and result.triangles == 12

    def test_missing_file_is_rejected(self, tmp_path):
        result = validate_stl_file(tmp_path / "nope.stl")
        assert not result.ok and "does not exist" in result.errors[0]

    def test_empty_file_is_rejected(self, tmp_path):
        path = tmp_path / "empty.stl"
        path.write_bytes(b"")
        assert not validate_stl_file(path).ok

    def test_truncated_file_is_rejected(self, tmp_path):
        path = tmp_path / "short.stl"
        path.write_bytes(b"\0" * 40)
        assert not validate_stl_file(path).ok

    def test_corrupt_triangle_count_is_rejected(self, tmp_path):
        result = validate_stl_file(corrupt_stl(tmp_path / "bad.stl"))
        assert not result.ok
        assert any("corrupt" in e or "does not match" in e for e in result.errors)

    def test_garbage_content_is_rejected(self, tmp_path):
        path = tmp_path / "garbage.stl"
        path.write_bytes(b"this is definitely not an STL file" * 10)
        assert not validate_stl_file(path).ok

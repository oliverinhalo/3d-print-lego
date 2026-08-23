"""ZIP structure and duplicate expansion.

Per-piece STLs are opt-in now that pre-arranged plates are the default, so
these tests pass include_stls=True explicitly.
"""
import json
import zipfile

import pytest

from app.models.job import Job
from app.models.part import GeometryRef, PartStatus, PrintPart
from app.models.set import LegoSet
from app.services.zip_service import ZipService
from tests.conftest import write_cube_stl


def make_job(quantities: dict[str, int], names: dict[str, str] | None = None) -> Job:
    names = names or {}
    job = Job(query="77263", set_num="77263-1")
    job.lego_set = LegoSet("77263-1", "Test Set", 2024, "Test",
                           sum(quantities.values()))
    for part_num, qty in quantities.items():
        job.parts[part_num] = PrintPart(
            part_num=part_num, name=names.get(part_num, f"Part {part_num}"),
            quantity=qty, status=PartStatus.READY,
            geometry=GeometryRef("fake", part_num, part_num))
    return job


@pytest.fixture
def zips(settings):
    return ZipService(settings.output_directory, settings.max_zip_bytes)


def geometry_for(job, tmp_path):
    return {p: write_cube_stl(tmp_path / f"{p}.stl") for p in job.parts}


class TestDuplicates:
    @pytest.mark.parametrize("quantity", [1, 2, 3, 20, 100])
    def test_each_copy_becomes_its_own_file(self, zips, tmp_path, quantity):
        job = make_job({"3001": quantity})
        result = zips.build(job, geometry_for(job, tmp_path), include_stls=True)

        with zipfile.ZipFile(result.path) as zf:
            stls = [n for n in zf.namelist() if n.endswith(".stl")]
        assert len(stls) == quantity
        assert result.instance_count == quantity

    def test_mixed_quantities_all_appear(self, zips, tmp_path):
        job = make_job({"3001": 20, "3024": 15, "3673": 40})
        result = zips.build(job, geometry_for(job, tmp_path), include_stls=True)

        with zipfile.ZipFile(result.path) as zf:
            names = [n for n in zf.namelist() if n.endswith(".stl")]
        assert len(names) == 75
        assert sum(1 for n in names if "/3001_" in n) == 20
        assert sum(1 for n in names if "/3024_" in n) == 15
        assert sum(1 for n in names if "/3673_" in n) == 40

    def test_duplicate_files_are_byte_identical(self, zips, tmp_path):
        job = make_job({"3001": 5})
        result = zips.build(job, geometry_for(job, tmp_path), include_stls=True)
        with zipfile.ZipFile(result.path) as zf:
            contents = {zf.read(n) for n in zf.namelist() if n.endswith(".stl")}
        assert len(contents) == 1, "all copies should come from one cached shape"

    def test_every_member_name_is_unique(self, zips, tmp_path):
        job = make_job({"3001": 30, "3024": 30})
        result = zips.build(job, geometry_for(job, tmp_path), include_stls=True)
        with zipfile.ZipFile(result.path) as zf:
            names = zf.namelist()
        assert len(names) == len(set(names))

    def test_parts_that_sanitise_alike_do_not_collide(self, zips, tmp_path):
        job = make_job({"3001": 1, "3001x": 1},
                       {"3001": "Brick 2 x 4", "3001x": "Brick 2 x 4"})
        result = zips.build(job, geometry_for(job, tmp_path), include_stls=True)
        with zipfile.ZipFile(result.path) as zf:
            assert len(zf.namelist()) == len(set(zf.namelist()))


class TestStructure:
    def test_expected_layout(self, zips, tmp_path):
        job = make_job({"3001": 2})
        result = zips.build(job, geometry_for(job, tmp_path), include_stls=True)

        with zipfile.ZipFile(result.path) as zf:
            names = zf.namelist()
        root = "LEGO_77263_Print_Pack"
        assert f"{root}/README.txt" in names
        assert f"{root}/parts.json" in names
        assert all(n.startswith(root + "/") for n in names)
        assert any(n.startswith(f"{root}/STLs/") for n in names)

    def test_stls_are_omitted_by_default(self, zips, tmp_path):
        job = make_job({"3001": 2})
        result = zips.build(job, geometry_for(job, tmp_path))
        with zipfile.ZipFile(result.path) as zf:
            assert not any(n.endswith(".stl") for n in zf.namelist())
            assert "LEGO_77263_Print_Pack/README.txt" in zf.namelist()

    def test_archive_is_named_after_the_set(self, zips, tmp_path):
        job = make_job({"3001": 1})
        assert zips.build(job, geometry_for(job, tmp_path), include_stls=True).name == \
            "LEGO_77263_Print_Pack.zip"

    def test_manifest_records_quantities(self, zips, tmp_path):
        job = make_job({"3001": 12, "3024": 8})
        result = zips.build(job, geometry_for(job, tmp_path), include_stls=True)

        with zipfile.ZipFile(result.path) as zf:
            manifest = json.loads(zf.read("LEGO_77263_Print_Pack/parts.json"))
        assert manifest["totals"]["stl_files"] == 20
        assert manifest["units"] == "millimetres"
        by_part = {p["part_num"]: p for p in manifest["parts"]}
        assert by_part["3001"]["quantity"] == 12
        assert by_part["3001"]["files"] == 12

    def test_readme_explains_the_workflow(self, zips, tmp_path):
        job = make_job({"3001": 1})
        result = zips.build(job, geometry_for(job, tmp_path), include_stls=True)
        with zipfile.ZipFile(result.path) as zf:
            readme = zf.read("LEGO_77263_Print_Pack/README.txt").decode()
        assert "millimetres" in readme
        assert "LDraw" in readme

    def test_the_archive_extracts_and_the_files_are_readable(self, zips, tmp_path):
        job = make_job({"3001": 3})
        result = zips.build(job, geometry_for(job, tmp_path), include_stls=True)
        target = tmp_path / "out"
        with zipfile.ZipFile(result.path) as zf:
            zf.extractall(target)
        stls = list((target / "LEGO_77263_Print_Pack" / "STLs").glob("*.stl"))
        assert len(stls) == 3

        from app.ldraw.stl import validate_stl_file
        assert all(validate_stl_file(p).ok for p in stls)


class TestPartialResults:
    def test_failed_parts_are_excluded_but_the_rest_ship(self, zips, tmp_path):
        job = make_job({"3001": 2, "9999": 5})
        job.parts["9999"].status = PartStatus.FAILED
        job.parts["9999"].error = "No 3D model is available for this part."

        geometry = {"3001": write_cube_stl(tmp_path / "3001.stl")}
        result = zips.build(job, geometry, include_stls=True)

        with zipfile.ZipFile(result.path) as zf:
            names = [n for n in zf.namelist() if n.endswith(".stl")]
            readme = zf.read("LEGO_77263_Print_Pack/README.txt").decode()
        assert len(names) == 2
        assert "9999" in readme, "the README must explain what is missing"

    def test_a_job_where_everything_failed_still_produces_a_readable_archive(
            self, zips, tmp_path):
        job = make_job({"9999": 3})
        job.parts["9999"].status = PartStatus.FAILED
        result = zips.build(job, {}, include_stls=True)
        with zipfile.ZipFile(result.path) as zf:
            assert zf.testzip() is None
            assert "LEGO_77263_Print_Pack/README.txt" in zf.namelist()


class TestCleanup:
    def test_job_directories_are_removable(self, zips, tmp_path):
        job = make_job({"3001": 1})
        result = zips.build(job, geometry_for(job, tmp_path), include_stls=True)
        zips.cleanup_job(job.id)
        assert not result.path.exists()

    @pytest.mark.parametrize("job_id", ["../escape", "..", "a/b", ""])
    def test_job_ids_cannot_escape_the_output_directory(self, zips, job_id):
        try:
            path = zips.job_directory(job_id)
        except ValueError:
            return                      # rejecting outright is fine too
        assert str(path).startswith(str(zips.root.resolve()))

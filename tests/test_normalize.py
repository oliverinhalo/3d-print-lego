"""Set-number parsing and filename sanitisation (spec section 33)."""
import pytest

from app.services.naming_service import instance_filename, short_part_name, zip_basename
from app.services.normalize import (InvalidSetNumber, display_number,
                                    normalize_set_number, sanitize_filename)


@pytest.mark.parametrize("raw", [
    "77263", "#77263", "LEGO 77263", "lego #77263", " 77263 ", "77263-1",
    "77263 - 1", "LEGO Set 77263", "Set #77263", "lego set no 77263",
])
def test_all_input_forms_normalise_to_one_id(raw):
    assert normalize_set_number(raw) == "77263-1"


def test_explicit_inventory_version_is_preserved():
    assert normalize_set_number("77263-2") == "77263-2"
    assert normalize_set_number("#10276-3") == "10276-3"


@pytest.mark.parametrize("raw", [
    "", "   ", "abc", "#", "-", "!!!", "x" * 100, None,
    "77263/../etc/passwd", "77263; DROP TABLE sets", "../../secret",
])
def test_invalid_input_is_rejected(raw):
    with pytest.raises(InvalidSetNumber):
        normalize_set_number(raw)


def test_display_number_strips_the_version():
    assert display_number("77263-1") == "77263"


class TestFilenameSafety:
    @pytest.mark.parametrize("char", list('/\\:*?"<>|'))
    def test_forbidden_characters_are_removed(self, char):
        assert char not in sanitize_filename(f"Brick{char}2x4")

    def test_control_characters_are_removed(self):
        assert sanitize_filename("Brick\x00\x1f2x4") == "Brick2x4"

    def test_traversal_cannot_survive(self):
        result = sanitize_filename("../../etc/passwd")
        assert "/" not in result and ".." not in result

    @pytest.mark.parametrize("name", ["CON", "PRN", "NUL", "COM1", "LPT1"])
    def test_windows_reserved_names_are_escaped(self, name):
        assert sanitize_filename(name) != name

    def test_empty_input_falls_back(self):
        assert sanitize_filename("") == "part"
        assert sanitize_filename("///") == "part"

    def test_length_is_bounded(self):
        assert len(sanitize_filename("A" * 500, max_length=32)) <= 32


class TestInstanceNaming:
    def test_expected_shape(self):
        assert instance_filename("3001", "Brick 2 x 4", 1, 3) == "3001_Brick_2_x_4_01.stl"

    def test_instances_are_zero_padded_to_the_total_width(self):
        assert instance_filename("3001", "Brick", 7, 12).endswith("_07.stl")
        assert instance_filename("3001", "Brick", 7, 120).endswith("_007.stl")

    def test_every_copy_of_a_part_gets_a_distinct_name(self):
        names = {instance_filename("3001", "Brick 2 x 4", i, 20) for i in range(1, 21)}
        assert len(names) == 20

    def test_long_catalogue_names_are_condensed(self):
        name = short_part_name(
            "Plate Special 1 x 2 with 1 Stud with Groove and Inside Stud Holder")
        assert len(name) <= 40 and "/" not in name

    def test_zip_basename(self):
        assert zip_basename("77263") == "LEGO_77263_Print_Pack"

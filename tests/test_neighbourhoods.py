"""Unit tests for neighbourhood slug generation (no network required)."""
from src.neighbourhoods import slugify


def test_slugify_basic():
    assert slugify("Fort York-Liberty Village") == "fort-york-liberty-village"


def test_slugify_collapses_and_trims_separators():
    assert slugify("  Cabbagetown—South St. James Town ") == "cabbagetown-south-st-james-town"


def test_slugify_is_filesystem_safe():
    slug = slugify("O'Connor-Parkview / Área 51")
    assert all(c.isalnum() or c == "-" for c in slug)
    assert not slug.startswith("-") and not slug.endswith("-")


def test_slugify_empty_falls_back():
    assert slugify("!!!") == "aoi"

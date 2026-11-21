"""Synthetic GeoJSON polygons: never infer district from a centroid or bbox."""
from geo_boundaries import membership


OUTER = [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]
HOLE = [[1, 1], [2, 1], [2, 2], [1, 2], [1, 1]]
SECOND = [[6, 6], [8, 6], [8, 8], [6, 8], [6, 6]]


def test_polygon_inclusion_exclusion_and_border():
    geom = {"type": "Polygon", "coordinates": [OUTER]}
    assert membership([3, 3], geom) == "inside"
    assert membership([5, 3], geom) == "outside"
    assert membership([4, 3], geom) == "unverified"


def test_holes_exclude_inner_area():
    geom = {"type": "Polygon", "coordinates": [OUTER, HOLE]}
    assert membership([0.5, 0.5], geom) == "inside"
    assert membership([1.5, 1.5], geom) == "outside"
    assert membership([1, 1.5], geom) == "unverified"


def test_multipolygon_supports_disjoint_areas():
    geom = {"type": "MultiPolygon", "coordinates": [[OUTER], [SECOND]]}
    assert membership([7, 7], geom) == "inside"
    assert membership([5, 5], geom) == "outside"


def test_bounding_box_centroid_and_invalid_geometry_are_not_proof():
    assert membership([1, 1], {"bbox": [0, 0, 4, 4]}) == "unverified"
    assert membership([1, 1], {"type": "Point", "coordinates": [1, 1]}) == "unverified"
    assert membership([1, 1], {"type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]}) == "unverified"
    assert membership([1, 1], {"type": "Polygon", "coordinates": []}) == "unverified"
    assert membership([float("nan"), 1], {"type": "Polygon", "coordinates": [OUTER]}) == "unverified"

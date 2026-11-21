"""Conservative GeoJSON point-in-polygon checks for independent location audits.

Only external, licensed boundary datasets can substantiate membership.
This module contains no real geography and makes no remote requests.
"""
import math


def _point(raw):
    if (not isinstance(raw, (list, tuple)) or len(raw) < 2
            or any(isinstance(x, bool) or not isinstance(x, (float, int))
                   for x in raw[:2])):
        return None
    lon, lat = raw[:2]
    if not math.isfinite(lon) or not math.isfinite(lat):
        return None
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    return lon, lat


def _ring_membership(point, ring):
    """True inside, False outside, None exactly on a border or invalid."""
    if not isinstance(ring, list) or len(ring) < 4:
        return None
    vertices = [_point(p) for p in ring]
    if any(p is None for p in vertices) or vertices[0] != vertices[-1]:
        return None
    x, y = point
    inside = False
    for a, b in zip(vertices, vertices[1:]):
        ax, ay = a
        bx, by = b
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if abs(cross) <= 1e-11 and (
                min(ax, bx) - 1e-11 <= x <= max(ax, bx) + 1e-11
                and min(ay, by) - 1e-11 <= y <= max(ay, by) + 1e-11):
            return None
        if (ay > y) != (by > y):
            intersect_x = ax + (y - ay) * (bx - ax) / (by - ay)
            if x < intersect_x:
                inside = not inside
    return inside


def _polygon_membership(point, polygon):
    if not isinstance(polygon, list) or not polygon:
        return None
    exterior = _ring_membership(point, polygon[0])
    if exterior is not True:
        return exterior
    for hole in polygon[1:]:
        hole_result = _ring_membership(point, hole)
        if hole_result is None:
            return None
        if hole_result:
            return False
    return True


def membership(point, geometry):
    """Return 'inside', 'outside' or 'unverified' for GeoJSON geometry.

    A bounding box, geocoding centroid or a district name is insufficient.
    Border points and invalid polygons are deliberately unverified.
    """
    xy = _point(point)
    if xy is None or not isinstance(geometry, dict):
        return "unverified"
    typ, data = geometry.get("type"), geometry.get("coordinates")
    if typ == "Polygon":
        result = _polygon_membership(xy, data)
    elif typ == "MultiPolygon":
        if not isinstance(data, list) or not data:
            return "unverified"
        results = [_polygon_membership(xy, p) for p in data]
        result = (None if None in results else True if True in results
                  else False)
    else:
        return "unverified"
    return "unverified" if result is None else "inside" if result else "outside"

"""Location helpers shared by importers and the static site builder."""
from __future__ import annotations

import math
import unicodedata

_QUEBEC_MIN_LAT = 44.8
_QUEBEC_MAX_LAT = 62.7
_QUEBEC_MIN_LNG = -79.9
_QUEBEC_MAX_LNG = -57.0
_EARTH_RADIUS_M = 6_371_000
_CITY_INFERENCE_RADIUS_M = 8_000

# Strava routes do not expose a locality like RideWithGPS does. Keep a small,
# deterministic set of common club departure hubs so imports and static builds
# can still display a useful start city without depending on a geocoding API.
_START_CITY_ANCHORS = (
    ("Lac-Brome", 45.2223, -72.5325),
    ("Bromont", 45.2704, -72.6712),
    ("Bromont", 45.3170, -72.6510),
    ("Bromont", 45.3433, -72.6489),
    ("Waterloo", 45.3355, -72.5120),
    ("Dunham", 45.1313, -72.8002),
    ("Frelighsburg", 45.0554, -72.8367),
    ("Ayer's Cliff", 45.1696, -72.0432),
    ("Sutton", 45.1098, -72.6167),
    ("Magog", 45.2665, -72.1470),
    ("Orford", 45.3112, -72.1884),
    ("North Hatley", 45.2769, -71.9748),
    ("Sherbrooke", 45.4042, -71.8929),
    ("Compton", 45.2358, -71.8272),
    ("Coaticook", 45.1334, -71.8047),
    ("Eastman", 45.3009, -72.3158),
    ("Valcourt", 45.4930, -72.3160),
    ("Granby", 45.4032, -72.7341),
    ("Ham-Nord", 45.8942, -71.6467),
    ("Victoriaville", 46.0501, -71.9658),
    ("Shawinigan", 46.5660, -72.7460),
    ("Rivière-du-Loup", 47.8988, -69.3280),
)


def _normalize_area(value) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def administrative_area_is_quebec(value) -> bool:
    area = _normalize_area(value)
    return area == "qc" or "quebec" in area


def point_is_in_quebec(lat, lng) -> bool:
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return False
    return (
        _QUEBEC_MIN_LAT <= lat <= _QUEBEC_MAX_LAT
        and _QUEBEC_MIN_LNG <= lng <= _QUEBEC_MAX_LNG
    )


def first_geometry_point(geometry) -> tuple[float, float] | None:
    for point in geometry or []:
        if isinstance(point, dict):
            lat = point.get("lat") or point.get("latitude") or point.get("y")
            lng = (
                point.get("lng")
                or point.get("lon")
                or point.get("longitude")
                or point.get("x")
            )
        else:
            try:
                lat, lng = point[0], point[1]
            except (TypeError, IndexError):
                continue
        try:
            return float(lat), float(lng)
        except (TypeError, ValueError):
            continue
    return None


def geometry_starts_in_quebec(geometry) -> bool:
    point = first_geometry_point(geometry)
    if not point:
        return False
    return point_is_in_quebec(*point)


def infer_start_city(geometry, *, max_distance_m: float = _CITY_INFERENCE_RADIUS_M) -> str:
    point = first_geometry_point(geometry)
    if not point:
        return ""

    matches = [
        (_distance_between_points_m(point, (lat, lng)), city)
        for city, lat, lng in _START_CITY_ANCHORS
    ]
    distance_m, city = min(matches, key=lambda match: match[0])
    if distance_m <= max_distance_m:
        return city
    return ""


def _distance_between_points_m(
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    ref_lat_rad = math.radians((a[0] + b[0]) / 2)
    ax, ay = _project_to_meters(a, ref_lat_rad)
    bx, by = _project_to_meters(b, ref_lat_rad)
    return math.hypot(ax - bx, ay - by)


def _project_to_meters(point: tuple[float, float], ref_lat_rad: float) -> tuple[float, float]:
    lat, lng = point
    return (
        math.radians(lng) * _EARTH_RADIUS_M * math.cos(ref_lat_rad),
        math.radians(lat) * _EARTH_RADIUS_M,
    )


def rwgps_route_starts_in_quebec(route: dict) -> bool:
    for key in ("administrative_area", "province", "state", "region"):
        value = route.get(key)
        if value:
            return administrative_area_is_quebec(value)
    return geometry_starts_in_quebec(route.get("track_points") or [])

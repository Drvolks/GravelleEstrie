"""Location filters shared by importers and the static site builder."""
from __future__ import annotations

import unicodedata

_QUEBEC_MIN_LAT = 44.8
_QUEBEC_MAX_LAT = 62.7
_QUEBEC_MIN_LNG = -79.9
_QUEBEC_MAX_LNG = -57.0


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


def geometry_starts_in_quebec(geometry) -> bool:
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
        return point_is_in_quebec(lat, lng)
    return False


def rwgps_route_starts_in_quebec(route: dict) -> bool:
    for key in ("administrative_area", "province", "state", "region"):
        value = route.get(key)
        if value:
            return administrative_area_is_quebec(value)
    return geometry_starts_in_quebec(route.get("track_points") or [])

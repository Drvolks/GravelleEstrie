"""Configured ravito detection for ride routes.

Ravitos are configured from environment variables and matched against stored
route geometry during static site generation.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import re
from typing import Iterable
from urllib.parse import unquote_plus, urlparse

import requests

EARTH_RADIUS_M = 6_371_000
URL_TIMEOUT_SECONDS = 8

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Ravito:
    name: str
    lat: float
    lng: float
    url: str = ""


@dataclass(frozen=True)
class RavitoMatch:
    ravito: Ravito
    distance_m: float
    route_distance_m: float
    remaining_distance_m: float


def parse_ravito_points(raw: str) -> list[Ravito]:
    """Parse configured ravitos from coordinates or Google Maps URLs.

    Supported entries:
    - ``Name|lat|lng``
    - ``https://maps.app.goo.gl/...`` or a full Google Maps URL
    - ``Name|https://maps.app.goo.gl/...`` to override the displayed name
    """
    ravitos = []
    for entry in _split_entries(raw):
        ravito = _parse_ravito_entry(entry)
        if ravito:
            ravitos.append(ravito)
    return ravitos


def _split_entries(raw: str) -> list[str]:
    normalized = re.sub(
        r",(?=\s*(?:[^|;,]+\|)?https?://)",
        ";",
        raw or "",
    )
    normalized = re.sub(
        r",(?=\s*[^|;,]+\|-?\d+(?:\.\d+)?\|)",
        ";",
        normalized,
    )
    return [entry.strip() for entry in re.split(r"[;\n]+", normalized) if entry.strip()]


def _parse_ravito_entry(entry: str) -> Ravito | None:
    parts = [part.strip() for part in entry.split("|")]
    if len(parts) == 3:
        ravito = _parse_coordinate_entry(parts)
        if ravito:
            return ravito
    if len(parts) == 2 and _looks_like_url(parts[1]):
        return _parse_url_entry(parts[1], name_override=parts[0])
    if len(parts) == 1 and _looks_like_url(parts[0]):
        return _parse_url_entry(parts[0])
    return None


def _parse_coordinate_entry(parts: list[str]) -> Ravito | None:
    if not parts[0]:
        return None
    try:
        lat = float(parts[1])
        lng = float(parts[2])
    except ValueError:
        return None
    if not _valid_coordinates(lat, lng):
        return None
    return Ravito(name=parts[0], lat=lat, lng=lng)


def _parse_url_entry(url: str, *, name_override: str = "") -> Ravito | None:
    resolved_url = url
    coordinates = _extract_coordinates(url)
    if not coordinates:
        resolved_url = _resolve_url(url)
        coordinates = _extract_coordinates(resolved_url)
    if not coordinates:
        logger.warning("Skipping ravito URL without coordinates: %s", url)
        return None
    lat, lng = coordinates
    name = name_override.strip() or _extract_place_name(resolved_url) or "Ravito"
    return Ravito(name=name, lat=lat, lng=lng, url=url)


def _resolve_url(url: str) -> str:
    try:
        response = requests.get(
            url,
            allow_redirects=True,
            headers={"User-Agent": "GravelleEstrie/1.0"},
            timeout=URL_TIMEOUT_SECONDS,
        )
        return response.url or url
    except requests.RequestException as exc:
        logger.warning("Could not resolve ravito URL %s: %s", url, exc)
        return url


def _extract_coordinates(url: str) -> tuple[float, float] | None:
    decoded_url = unquote_plus(url)
    for pattern in (
        r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
        r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
        r"[?&](?:q|query|ll)=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
    ):
        match = re.search(pattern, decoded_url)
        if not match:
            continue
        try:
            lat = float(match.group(1))
            lng = float(match.group(2))
        except ValueError:
            continue
        if _valid_coordinates(lat, lng):
            return lat, lng
    return None


def _extract_place_name(url: str) -> str:
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    for marker in ("place", "search"):
        if marker not in path_parts:
            continue
        index = path_parts.index(marker) + 1
        if index < len(path_parts):
            return unquote_plus(path_parts[index]).strip()
    return ""


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _valid_coordinates(lat: float, lng: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lng <= 180


def find_nearby_ravitos(
    geometry: Iterable[Iterable[float]],
    ravitos: Iterable[Ravito],
    radius_m: float,
    min_route_distance_m: float = 0,
    endpoint_exclusion_radius_m: float = 0,
) -> list[RavitoMatch]:
    """Return ravitos within ``radius_m`` of the route geometry, closest first."""
    points = _geometry_points(geometry)
    if len(points) < 2 or radius_m <= 0:
        return []

    matches = []
    for ravito in ravitos:
        distance, route_distance, route_length = _nearest_route_position_m(
            (ravito.lat, ravito.lng),
            points,
        )
        remaining_distance = max(0.0, route_length - route_distance)
        if distance <= radius_m and _route_position_is_relevant(
            route_distance,
            min_route_distance_m,
        ) and not _is_near_route_endpoint(
            (ravito.lat, ravito.lng),
            points,
            endpoint_exclusion_radius_m,
        ):
            matches.append(
                RavitoMatch(
                    ravito=ravito,
                    distance_m=distance,
                    route_distance_m=route_distance,
                    remaining_distance_m=remaining_distance,
                )
            )

    return sorted(matches, key=lambda match: (match.distance_m, match.ravito.name.lower()))


def _geometry_points(geometry: Iterable[Iterable[float]]) -> list[tuple[float, float]]:
    points = []
    for point in geometry or []:
        try:
            lat, lng = point[0], point[1]
        except (TypeError, IndexError):
            continue
        try:
            points.append((float(lat), float(lng)))
        except (TypeError, ValueError):
            continue
    return points


def _nearest_route_position_m(
    target: tuple[float, float],
    points: list[tuple[float, float]],
) -> tuple[float, float, float]:
    best_distance = math.inf
    best_route_distance = 0.0
    cumulative_distance = 0.0

    for start, end in zip(points, points[1:]):
        distance, segment_position, segment_length = _nearest_segment_position_m(
            target,
            start,
            end,
        )
        if distance < best_distance:
            best_distance = distance
            best_route_distance = cumulative_distance + segment_position
        cumulative_distance += segment_length

    return best_distance, best_route_distance, cumulative_distance


def _nearest_segment_position_m(
    target: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[float, float, float]:
    ref_lat_rad = math.radians((target[0] + start[0] + end[0]) / 3)
    px, py = _project_to_meters(target, ref_lat_rad)
    ax, ay = _project_to_meters(start, ref_lat_rad)
    bx, by = _project_to_meters(end, ref_lat_rad)

    dx = bx - ax
    dy = by - ay
    segment_length = math.hypot(dx, dy)
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay), 0.0, 0.0

    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    nearest_x = ax + t * dx
    nearest_y = ay + t * dy
    return math.hypot(px - nearest_x, py - nearest_y), t * segment_length, segment_length


def _route_position_is_relevant(
    route_distance_m: float,
    min_route_distance_m: float,
) -> bool:
    if min_route_distance_m <= 0:
        return True
    return route_distance_m >= min_route_distance_m


def _is_near_route_endpoint(
    target: tuple[float, float],
    points: list[tuple[float, float]],
    endpoint_exclusion_radius_m: float,
) -> bool:
    if endpoint_exclusion_radius_m <= 0:
        return False
    return (
        _distance_between_points_m(target, points[0]) <= endpoint_exclusion_radius_m
        or _distance_between_points_m(target, points[-1]) <= endpoint_exclusion_radius_m
    )


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
        math.radians(lng) * EARTH_RADIUS_M * math.cos(ref_lat_rad),
        math.radians(lat) * EARTH_RADIUS_M,
    )

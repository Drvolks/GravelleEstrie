"""Configured ravito detection for ride routes.

Ravitos are configured from environment variables and matched against stored
route geometry during static site generation.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import re
from typing import Iterable
from urllib.parse import unquote_plus, urlparse

import requests

EARTH_RADIUS_M = 6_371_000
URL_TIMEOUT_SECONDS = 8
URL_CACHE_ENV = "GOOGLE_MAPS_URL_CACHE_PATH"
DEFAULT_URL_CACHE_PATH = Path(".cache/google-maps-url-cache.json")

logger = logging.getLogger(__name__)

_url_cache: dict[str, str] | None = None
_url_cache_path: Path | None = None


@dataclass(frozen=True)
class Ravito:
    name: str
    lat: float
    lng: float
    url: str = ""
    name_is_override: bool = False


@dataclass(frozen=True)
class RavitoMatch:
    ravito: Ravito
    distance_m: float
    route_distance_m: float
    remaining_distance_m: float


@dataclass(frozen=True)
class ParkingMatch:
    parking: Ravito
    distance_m: float


@dataclass(frozen=True)
class PlaisirMatch:
    plaisir: Ravito
    distance_m: float


def parse_ravito_points(raw: str) -> list[Ravito]:
    """Parse configured ravitos from coordinates or Google Maps URLs.

    Supported entries:
    - ``Name|lat|lng``
    - ``https://maps.app.goo.gl/...`` or a full Google Maps URL
    - ``Name|https://maps.app.goo.gl/...`` to override the displayed name
    """
    return parse_map_points(raw, default_name="Ravito")


def parse_parking_points(raw: str) -> list[Ravito]:
    return parse_map_points(raw, default_name="Stationnement")


def parse_plaisir_points(raw: str) -> list[Ravito]:
    return parse_map_points(raw, default_name="Plaisir")


def parse_map_points(raw: str, *, default_name: str = "Point") -> list[Ravito]:
    ravitos = []
    for entry in _split_entries(raw):
        ravito = _parse_ravito_entry(entry, default_name=default_name)
        if ravito:
            ravitos.append(ravito)
    return _dedupe_points(ravitos, default_name=default_name)


def _dedupe_points(points: list[Ravito], *, default_name: str) -> list[Ravito]:
    deduped: dict[str, Ravito] = {}
    for point in points:
        key = point.url or f"{point.lat:.6f},{point.lng:.6f}"
        existing = deduped.get(key)
        if (
            existing is None
            or (point.name_is_override and not existing.name_is_override)
            or (
                not existing.name_is_override
                and existing.name == default_name
                and point.name != default_name
            )
        ):
            deduped[key] = point
    return list(deduped.values())


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


def _parse_ravito_entry(entry: str, *, default_name: str) -> Ravito | None:
    parts = [part.strip() for part in entry.split("|")]
    if len(parts) == 3:
        ravito = _parse_coordinate_entry(parts)
        if ravito:
            return ravito
    if len(parts) == 2 and _looks_like_url(parts[1]):
        return _parse_url_entry(parts[1], name_override=parts[0], default_name=default_name)
    if len(parts) == 1 and _looks_like_url(parts[0]):
        return _parse_url_entry(parts[0], default_name=default_name)
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
    return Ravito(name=parts[0], lat=lat, lng=lng, name_is_override=True)


def _parse_url_entry(
    url: str,
    *,
    name_override: str = "",
    default_name: str = "Ravito",
) -> Ravito | None:
    resolved_url = url
    coordinates = _extract_coordinates(url)
    if not coordinates:
        resolved_url = _resolve_url(url)
        coordinates = _extract_coordinates(resolved_url)
    if not coordinates:
        logger.warning("Skipping ravito URL without coordinates: %s", url)
        return None
    lat, lng = coordinates
    override_name = name_override.strip()
    name = override_name or _extract_place_name(resolved_url) or default_name
    return Ravito(
        name=name,
        lat=lat,
        lng=lng,
        url=url,
        name_is_override=bool(override_name),
    )


def _resolve_url(url: str) -> str:
    cached_url = _cached_resolved_url(url)
    if cached_url:
        return cached_url

    try:
        response = requests.get(
            url,
            allow_redirects=True,
            headers={"User-Agent": "GravelleEstrie/1.0"},
            timeout=URL_TIMEOUT_SECONDS,
        )
        resolved_url = response.url or url
    except requests.RequestException as exc:
        logger.warning("Could not resolve ravito URL %s: %s", url, exc)
        return url
    if resolved_url != url and _extract_coordinates(resolved_url):
        _remember_resolved_url(url, resolved_url)
    return resolved_url


def _cached_resolved_url(url: str) -> str:
    cache = _load_url_cache()
    cached_url = cache.get(url, "")
    if cached_url and _extract_coordinates(cached_url):
        return cached_url
    return ""


def _remember_resolved_url(url: str, resolved_url: str) -> None:
    path = _current_url_cache_path()
    if path is None:
        return
    cache = _load_url_cache()
    if cache.get(url) == resolved_url:
        return
    cache[url] = resolved_url
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.tmp")
        tmp_path.write_text(
            json.dumps(dict(sorted(cache.items())), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except OSError as exc:
        logger.warning("Could not write Google Maps URL cache %s: %s", path, exc)


def _load_url_cache() -> dict[str, str]:
    global _url_cache, _url_cache_path

    path = _current_url_cache_path()
    if path is None:
        return {}
    if _url_cache is not None and _url_cache_path == path:
        return _url_cache

    _url_cache_path = path
    try:
        raw_cache = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _url_cache = {}
        return _url_cache
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read Google Maps URL cache %s: %s", path, exc)
        _url_cache = {}
        return _url_cache

    if not isinstance(raw_cache, dict):
        logger.warning("Ignoring invalid Google Maps URL cache %s", path)
        _url_cache = {}
        return _url_cache

    _url_cache = {
        str(source_url): str(resolved_url)
        for source_url, resolved_url in raw_cache.items()
        if _looks_like_url(str(source_url)) and _looks_like_url(str(resolved_url))
    }
    return _url_cache


def _current_url_cache_path() -> Path | None:
    raw_path = os.environ.get(URL_CACHE_ENV, "").strip()
    if raw_path.lower() in {"0", "false", "no", "off", "none"}:
        return None
    return Path(raw_path) if raw_path else DEFAULT_URL_CACHE_PATH


def _extract_coordinates(url: str) -> tuple[float, float] | None:
    decoded_url = unquote_plus(url)
    for pattern in (
        r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
        r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
        r"[?&](?:q|query|ll)=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
        r"/(?:search|place)/(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)",
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


def find_nearby_parking(
    geometry: Iterable[Iterable[float]],
    parkings: Iterable[Ravito],
    radius_m: float,
) -> list[ParkingMatch]:
    """Return configured parking points near the route start point."""
    return [
        ParkingMatch(parking=point, distance_m=distance)
        for point, distance in _find_near_endpoint(geometry, parkings, radius_m, index=0)
    ]


def find_nearby_plaisirs(
    geometry: Iterable[Iterable[float]],
    plaisirs: Iterable[Ravito],
    radius_m: float,
) -> list[PlaisirMatch]:
    """Return configured post-ride spots near the route finish point."""
    return [
        PlaisirMatch(plaisir=point, distance_m=distance)
        for point, distance in _find_near_endpoint(geometry, plaisirs, radius_m, index=-1)
    ]


def _find_near_endpoint(
    geometry: Iterable[Iterable[float]],
    candidates: Iterable[Ravito],
    radius_m: float,
    *,
    index: int,
) -> list[tuple[Ravito, float]]:
    points = _geometry_points(geometry)
    if not points or radius_m <= 0:
        return []

    endpoint = points[index]
    matches = []
    for candidate in candidates:
        distance = _distance_between_points_m((candidate.lat, candidate.lng), endpoint)
        if distance <= radius_m:
            matches.append((candidate, distance))

    return sorted(matches, key=lambda match: (match[1], match[0].name.lower()))


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

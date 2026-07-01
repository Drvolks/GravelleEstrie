"""Helpers for working with route geometry.

Geometry is stored throughout the project as a list of ``[lat, lng]`` pairs.
"""
from __future__ import annotations

from typing import Iterable

import polyline as _polyline


def decode_polyline(encoded: str, precision: int = 5) -> list[list[float]]:
    """Decode a Google/Strava encoded polyline into ``[lat, lng]`` pairs."""
    if not encoded:
        return []
    return [list(pt) for pt in _polyline.decode(encoded, precision)]


def downsample(points: list[list[float]], max_points: int = 400) -> list[list[float]]:
    """Reduce the number of points while keeping the first and last.

    Keeps thumbnails fast to render and JSON payloads small without visibly
    changing the shape of a route.
    """
    n = len(points)
    if n <= max_points:
        return points
    step = n / max_points
    out = [points[int(i * step)] for i in range(max_points)]
    if out[-1] != points[-1]:
        out[-1] = points[-1]
    return out


def bounds(points: Iterable[Iterable[float]]) -> tuple[float, float, float, float] | None:
    """Return (min_lat, min_lng, max_lat, max_lng) or None if empty."""
    pts = list(points)
    if not pts:
        return None
    lats = [p[0] for p in pts]
    lngs = [p[1] for p in pts]
    return min(lats), min(lngs), max(lats), max(lngs)

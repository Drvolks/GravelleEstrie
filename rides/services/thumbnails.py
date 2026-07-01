"""Render static PNG map thumbnails from route geometry.

Uses the `staticmap` library (OSM tiles + a drawn polyline). Runs at import
time so the published static site needs no map API keys or JavaScript.
"""
from __future__ import annotations

import io

from django.core.files.base import ContentFile
from staticmap import Line, StaticMap

from .geometry import downsample

# staticmap expects (lng, lat) coordinate pairs.
_TILE_URL = "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"
_LINE_COLOR = "#d64541"  # club red
_LINE_WIDTH = 4


def render_thumbnail(
    geometry: list[list[float]],
    width: int = 600,
    height: int = 360,
    padding: int = 24,
) -> bytes | None:
    """Return PNG bytes for the given ``[lat, lng]`` geometry, or None.

    Requires network access to fetch OSM tiles.
    """
    if not geometry or len(geometry) < 2:
        return None

    pts = downsample(geometry, max_points=500)
    # Convert [lat, lng] -> (lng, lat) for staticmap.
    coords = [(pt[1], pt[0]) for pt in pts]

    smap = StaticMap(width, height, padding_x=padding, padding_y=padding, url_template=_TILE_URL)
    smap.add_line(Line(coords, _LINE_COLOR, _LINE_WIDTH))

    image = smap.render()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def build_thumbnail_file(ride) -> ContentFile | None:
    """Render a thumbnail for a Ride and return a Django ContentFile.

    Does not save the model; caller assigns it to ``ride.thumbnail`` and saves.
    """
    png = render_thumbnail(ride.geometry)
    if png is None:
        return None
    return ContentFile(png, name=f"{ride.slug or 'ride'}.png")

"""Local ride image discovery.

Images live outside Django's database in ``images/<ride id>/``. The folder is
kept git-ignored locally and copied into the static site during ``build_site``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from rides.models import Ride


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


@dataclass(frozen=True)
class RideImage:
    path: Path
    filename: str
    folder_name: str


def ride_image_folder_names(ride: Ride) -> list[str]:
    """Return supported local folder names for a ride, in lookup order."""
    values = [
        ride.rwgps_route_id,
        ride.strava_activity_id,
        ride.external_id,
        str(ride.pk) if ride.pk else "",
        ride.slug,
    ]
    names: list[str] = []
    for value in values:
        name = str(value or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def expected_ride_image_dir(ride: Ride) -> Path:
    names = ride_image_folder_names(ride)
    folder_name = names[0] if names else str(ride.pk or ride.slug or "")
    return Path(settings.LOCAL_RIDE_IMAGES_DIR) / folder_name


def list_ride_images(ride: Ride) -> list[RideImage]:
    """List images from the first matching local image folder for ``ride``."""
    root = Path(settings.LOCAL_RIDE_IMAGES_DIR)
    for folder_name in ride_image_folder_names(ride):
        folder = root / folder_name
        if not folder.is_dir():
            continue
        images = [
            RideImage(path=path, filename=path.name, folder_name=folder_name)
            for path in sorted(folder.iterdir(), key=lambda p: p.name.lower())
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ]
        if images:
            return images
    return []

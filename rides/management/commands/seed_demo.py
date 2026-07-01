"""Create a handful of demo rides with real Estrie coordinates.

Useful for developing the site locally before Strava/RideWithGPS credentials
are configured. Idempotent: re-running updates the same demo rides.
"""
from __future__ import annotations

import math
from datetime import date

from django.core.management.base import BaseCommand

from rides.models import Ride
from rides.services.thumbnails import build_thumbnail_file

# (name, start_city, center lat/lng, distance_km, elevation_m, date)
_DEMO = [
    ("Boucle du Lac Memphrémagog", "Magog", 45.2686, -72.1461, 62.0, 780, date(2026, 5, 18)),
    ("Gravelle du Mont-Orford", "Orford", 45.3160, -72.2200, 48.5, 1120, date(2026, 6, 1)),
    ("Sentiers de Sherbrooke", "Sherbrooke", 45.4042, -71.8929, 35.2, 540, date(2026, 6, 15)),
    ("Rando North Hatley", "North Hatley", 45.2820, -71.9760, 74.8, 960, date(2026, 6, 29)),
]


def _loop(center_lat, center_lng, points=120, radius_km=6.0):
    """Generate a wobbly closed loop around a center point."""
    geo = []
    lat_r = radius_km / 111.0
    lng_r = radius_km / (111.0 * math.cos(math.radians(center_lat)))
    for i in range(points):
        t = 2 * math.pi * i / points
        wobble = 1 + 0.25 * math.sin(3 * t) + 0.1 * math.cos(5 * t)
        geo.append([
            round(center_lat + lat_r * wobble * math.sin(t), 6),
            round(center_lng + lng_r * wobble * math.cos(t), 6),
        ])
    geo.append(geo[0])
    return geo


class Command(BaseCommand):
    help = "Create/refresh demo rides (with map thumbnails)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-thumbnails", action="store_true",
            help="Skip thumbnail rendering (avoids OSM tile downloads).",
        )

    def handle(self, *args, **options):
        render = not options["no_thumbnails"]
        for name, city, lat, lng, dist_km, elev, d in _DEMO:
            ride, created = Ride.objects.update_or_create(
                source=Ride.Source.MANUAL,
                external_id=f"demo-{name}",
                defaults={
                    "name": name,
                    "start_city": city,
                    "distance_m": dist_km * 1000,
                    "elevation_gain_m": elev,
                    "ride_date": d,
                    "geometry": _loop(lat, lng),
                    "description": f"Sortie démo autour de {city}, dans les Cantons-de-l'Est.",
                    "strava_url": "https://www.strava.com/athletes/89793076",
                    "ridewithgps_url": "https://ridewithgps.com/users/4058724",
                },
            )
            verb = "créée" if created else "mise à jour"
            if render:
                try:
                    thumb = build_thumbnail_file(ride)
                    if thumb is not None:
                        ride.thumbnail.save(thumb.name, thumb, save=True)
                        verb += " + vignette"
                except Exception as exc:  # noqa: BLE001
                    self.stderr.write(self.style.WARNING(f"{name}: vignette échouée ({exc})"))
            self.stdout.write(self.style.SUCCESS(f"{name} — {verb}"))

"""Generate the static website published to GitHub Pages.

Renders an index (with client-side search/filter) plus one detail page per
published ride into ``settings.SITE_OUTPUT_DIR``. Fully self-contained: copies
the pre-rendered thumbnail PNGs and static assets, no runtime API or JS map.
"""
from __future__ import annotations

import math
import shutil
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from django.conf import settings
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string

from rides.models import Ride
from rides.services.location import geometry_starts_in_quebec


class Command(BaseCommand):
    help = "Build the static site into SITE_OUTPUT_DIR."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            help="Override the output directory (defaults to SITE_OUTPUT_DIR).",
        )

    def handle(self, *args, **options):
        out = Path(options["output"]) if options.get("output") else settings.SITE_OUTPUT_DIR
        base_path = settings.SITE_BASE_PATH

        # Clear the directory's *contents* rather than the directory itself,
        # so it works when `out` is a mounted volume (e.g. in Docker).
        out.mkdir(parents=True, exist_ok=True)
        for child in out.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

        self._copy_assets(out)
        thumbs_dir = out / "assets" / "thumbs"
        thumbs_dir.mkdir(parents=True, exist_ok=True)

        rides_qs = Ride.objects.published()
        if settings.RWGPS_EXCLUDED_ROUTE_IDS:
            rides_qs = rides_qs.exclude(rwgps_route_id__in=settings.RWGPS_EXCLUDED_ROUTE_IDS)
        rides = [ride for ride in rides_qs if geometry_starts_in_quebec(ride.geometry)]
        views = [self._ride_view(r, base_path, thumbs_dir) for r in rides]

        max_distance = self._ceil_max((v.distance_km for v in views), default=100, step=10)
        max_elevation = self._ceil_max((v.elevation_m for v in views), default=1000, step=100)

        common = {
            "base_path": base_path,
            "site_title": settings.SITE_TITLE,
            "site_tagline": settings.SITE_TAGLINE,
        }

        # Index
        (out / "index.html").write_text(
            render_to_string(
                "site/index.html",
                {**common, "rides": views, "max_distance": max_distance, "max_elevation": max_elevation},
            ),
            encoding="utf-8",
        )

        # Detail pages at /rides/<slug>/index.html
        for view in views:
            ride_dir = out / "rides" / view.slug
            ride_dir.mkdir(parents=True, exist_ok=True)
            (ride_dir / "index.html").write_text(
                render_to_string("site/detail.html", {**common, "ride": view}),
                encoding="utf-8",
            )

        # Tell GitHub Pages not to run Jekyll (keeps files predictable).
        (out / ".nojekyll").write_text("", encoding="utf-8")

        self.stdout.write(
            self.style.SUCCESS(
                f"Site généré : {len(views)} sortie(s) → {out}"
            )
        )

    # -- helpers ------------------------------------------------------------

    def _copy_assets(self, out: Path):
        src = Path(settings.BASE_DIR) / "rides" / "static_src"
        dest = out / "assets"
        shutil.copytree(src, dest, dirs_exist_ok=True)

    def _ride_view(self, ride: Ride, base_path: str, thumbs_dir: Path) -> SimpleNamespace:
        thumb_url = ""
        if ride.thumbnail and Path(ride.thumbnail.path).exists():
            dest = thumbs_dir / f"{ride.slug}.png"
            shutil.copyfile(ride.thumbnail.path, dest)
            thumb_url = f"{base_path}/assets/thumbs/{ride.slug}.png"

        return SimpleNamespace(
            name=ride.name,
            slug=ride.slug,
            description=ride.description,
            ride_date=ride.ride_date,
            start_city=ride.start_city,
            distance_km=ride.distance_km,
            elevation_m=ride.elevation_m,
            strava_url=ride.strava_url,
            ridewithgps_url=ride.ridewithgps_url,
            ridewithgps_embed_url=self._ridewithgps_embed_url(ride),
            source_label=ride.get_source_display(),
            thumb_url=thumb_url,
        )

    @staticmethod
    def _ridewithgps_embed_url(ride: Ride) -> str:
        if not ride.rwgps_route_id:
            return ""
        query = urlencode(
            {
                "type": "route",
                "id": ride.rwgps_route_id,
                "sampleGraph": "true",
            }
        )
        return f"https://ridewithgps.com/embeds?{query}"

    @staticmethod
    def _ceil_max(values, *, default: int, step: int) -> int:
        vals = [v for v in values if v]
        if not vals:
            return default
        top = max(vals)
        return int(math.ceil(top / step) * step)

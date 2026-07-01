"""Import rides from RideWithGPS, then match Strava routes onto them.

Runs RideWithGPS first because it's the primary, bulk source (its API can
list an arbitrary user's routes) — Strava's API has no equivalent listing
capability for other athletes, so it only ever contributes whatever routes
are manually listed in STRAVA_ROUTE_IDS, matched onto the RideWithGPS rides
just imported.
"""
from django.core.management.base import BaseCommand

from rides.services.importer import import_ridewithgps, import_strava
from rides.services.ridewithgps import RideWithGPSError
from rides.services.strava import StravaError


class Command(BaseCommand):
    help = (
        "Import from RideWithGPS (bulk), then import Strava routes from "
        "STRAVA_ROUTE_IDS if configured (matched onto the RideWithGPS rides "
        "just imported)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-thumbnails",
            action="store_true",
            help="Skip rendering map thumbnails (faster, no tile downloads).",
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help="Fetch and update routes even when their source ids already exist locally.",
        )

    def handle(self, *args, **options):
        render = not options["no_thumbnails"]
        full = options["full"]

        try:
            rwgps_result = import_ridewithgps(render_thumbnails=render, full=full)
        except RideWithGPSError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return
        self.stdout.write(self.style.SUCCESS(f"RideWithGPS: {rwgps_result}"))
        for err in rwgps_result.errors:
            self.stderr.write(self.style.WARNING(err))

        try:
            strava_result = import_strava(render_thumbnails=render, full=full)
        except StravaError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return
        self.stdout.write(self.style.SUCCESS(f"Strava: {strava_result}"))
        for err in strava_result.errors:
            self.stderr.write(self.style.WARNING(err))

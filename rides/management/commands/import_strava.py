from django.core.management.base import BaseCommand

from rides.services.importer import import_strava
from rides.services.strava import StravaError


class Command(BaseCommand):
    help = (
        "Import routes listed in STRAVA_ROUTE_IDS (optional, secondary source "
        "— see README for why Strava can't be bulk-imported). Each route is "
        "matched onto an existing RideWithGPS-sourced ride if one exists "
        "(same name, similar distance) and merged in; otherwise a new ride "
        "is created."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-thumbnails",
            action="store_true",
            help="Skip rendering map thumbnails (faster, no tile downloads).",
        )
        parser.add_argument(
            "--require-rwgps-match",
            action="store_true",
            help="Only merge onto an existing RideWithGPS-sourced ride; skip routes with no match instead of creating one.",
        )

    def handle(self, *args, **options):
        try:
            result = import_strava(
                render_thumbnails=not options["no_thumbnails"],
                create_if_unmatched=not options["require_rwgps_match"],
            )
        except StravaError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return
        self.stdout.write(self.style.SUCCESS(f"Strava: {result}"))
        for err in result.errors:
            self.stderr.write(self.style.WARNING(err))

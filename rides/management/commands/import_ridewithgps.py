from django.core.management.base import BaseCommand

from rides.services.importer import import_ridewithgps
from rides.services.ridewithgps import RideWithGPSError


class Command(BaseCommand):
    help = (
        "Import all routes from the configured RideWithGPS user — the "
        "primary, bulk import source (see README). Each route is matched "
        "onto an existing Strava-sourced ride if one exists (same name, "
        "similar distance) and merged in; otherwise a new ride is created."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-thumbnails",
            action="store_true",
            help="Skip rendering map thumbnails (faster, no tile downloads).",
        )
        parser.add_argument(
            "--require-strava-match",
            action="store_true",
            help="Only merge onto an existing Strava-sourced ride; skip routes with no match instead of creating one.",
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help="Fetch and update routes even when their RideWithGPS ids already exist locally.",
        )

    def handle(self, *args, **options):
        try:
            result = import_ridewithgps(
                render_thumbnails=not options["no_thumbnails"],
                create_if_unmatched=not options["require_strava_match"],
                full=options["full"],
            )
        except RideWithGPSError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return
        self.stdout.write(self.style.SUCCESS(f"RideWithGPS: {result}"))
        for err in result.errors:
            self.stderr.write(self.style.WARNING(err))

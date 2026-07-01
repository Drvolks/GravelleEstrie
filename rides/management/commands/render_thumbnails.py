"""(Re)render map thumbnails for rides from their stored geometry.

Used in CI after loading a fixture (which stores thumbnail paths but not the
image bytes) and any time thumbnails need regenerating.
"""
from django.core.management.base import BaseCommand

from rides.models import Ride
from rides.services.thumbnails import build_thumbnail_file


class Command(BaseCommand):
    help = "Render thumbnails for rides (default: only those missing one)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all", action="store_true", help="Re-render every ride, even if it has a thumbnail."
        )

    def handle(self, *args, **options):
        qs = Ride.objects.all()
        if not options["all"]:
            qs = qs.filter(thumbnail="")
        done, skipped, failed = 0, 0, 0
        for ride in qs:
            if not ride.has_geometry:
                skipped += 1
                continue
            try:
                thumb = build_thumbnail_file(ride)
                if thumb is None:
                    skipped += 1
                    continue
                ride.thumbnail.save(thumb.name, thumb, save=True)
                done += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.stderr.write(self.style.WARNING(f"{ride.name}: {exc}"))
        self.stdout.write(
            self.style.SUCCESS(f"{done} rendue(s), {skipped} ignorée(s), {failed} échec(s).")
        )

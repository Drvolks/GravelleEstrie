"""One-off cleanup for ride names imported before the "(copier)" strip existed.

New imports already get a clean name (see ``services/importer.py``); this
command re-applies the same cleaning to rides already stored in the database.
Safe to re-run — it's a no-op once every name is already clean.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from rides.models import Ride
from rides.services.importer import clean_ride_name


class Command(BaseCommand):
    help = 'Strip the "(copier)"/"(copy)" suffix from existing ride names.'

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without saving.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        changed = 0
        for ride in Ride.objects.all():
            cleaned = clean_ride_name(ride.name)
            if cleaned != ride.name:
                changed += 1
                self.stdout.write(f"{ride.name!r} -> {cleaned!r}")
                if not dry_run:
                    ride.name = cleaned
                    ride.save(update_fields=["name"])
        verb = "would update" if dry_run else "updated"
        self.stdout.write(self.style.SUCCESS(f"{changed} ride(s) {verb}."))

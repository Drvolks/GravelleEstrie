"""Upsert imported rides into the database and render their thumbnails.

Both Strava and RideWithGPS import *routes/courses* here, not individually
recorded activities — see ``strava.py`` and ``ridewithgps.py``. RideWithGPS is
the primary, bulk source (its API can list an arbitrary user's routes, with
pagination — scales to hundreds with no manual work, plus explicit direct ids
from ``RWGPS_EXTRA_ROUTE_IDS``) and normally runs first (see the ``import``
management command). Strava is secondary and optional: its public API has no
way to list another athlete's routes at all (only the
authenticated athlete's own — see ``strava.py``), so it only ever imports
whatever handful of route ids you've manually collected into
``STRAVA_ROUTE_IDS``. Each Strava route is matched onto an existing
RideWithGPS-sourced ride by name + similar distance and merged in (or created
standalone if genuinely new — see ``import_strava``).

Matching does not use ``ride_date``: neither source's route object has the
day the club actually rode it (only, at most, a *creation* date), so it isn't
a reliable signal — ``ride_date`` is left blank on auto-imported rides and is
expected to be filled in manually in the admin. The ride name is expected to
be kept consistent between the two platforms and is the primary match key;
distance is a secondary sanity check.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from rides.models import Ride

from .ridewithgps import RideWithGPSClient, RWGPSRide
from .strava import StravaClient, StravaRide
from .thumbnails import build_thumbnail_file

logger = logging.getLogger(__name__)

# How close two rides' distances need to be (as a fraction of the larger) to
# be considered "the same ride" when matching across sources.
_DISTANCE_TOLERANCE = 0.15

# Both sources sometimes carry a "(copier)"/"(copy)" suffix left over from
# duplicating a route in the source app before tweaking it — not meaningful
# to riders, so it's stripped from the name we store.
_COPY_SUFFIX_RE = re.compile(r"\s*\((?:copier|copy)\)\s*$", re.IGNORECASE)


def clean_ride_name(name: str) -> str:
    name = (name or "").strip()
    name = _COPY_SUFFIX_RE.sub("", name).strip()
    return name


def _normalize_name(name: str) -> str:
    return " ".join(clean_ride_name(name).casefold().split())


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    merged: int = 0
    skipped: int = 0
    thumbnails: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    def __str__(self):
        return (
            f"{self.created} créées, {self.updated} mises à jour, "
            f"{self.merged} fusionnées (autre source), {self.skipped} ignorées, "
            f"{self.thumbnails} vignettes, {len(self.errors)} erreurs"
        )


def _distance_close(a: float, b: float, tolerance: float = _DISTANCE_TOLERANCE) -> bool:
    if not a or not b:
        return False
    return abs(a - b) <= tolerance * max(a, b)


def _find_cross_source_match(payload, *, id_field: str, other_id_field: str) -> Ride | None:
    """Look for a ride from the other source that is plausibly this same ride."""
    target_name = _normalize_name(payload.name)
    if not target_name:
        return None
    candidates = Ride.objects.filter(**{id_field: ""}).exclude(**{other_id_field: ""})
    for candidate in candidates:
        if _normalize_name(candidate.name) != target_name:
            continue
        if _distance_close(candidate.distance_m, payload.distance_m):
            return candidate
    return None


def _upsert(
    source: str,
    payload,
    *,
    id_field: str,
    other_id_field: str,
    render_thumbnails: bool,
    result: ImportResult,
    create_if_unmatched: bool = True,
):
    if not payload.geometry:
        result.skipped += 1
        logger.warning("Skipping %r (%s): no route geometry", payload.name, payload.external_id)
        return

    is_strava = isinstance(payload, StravaRide)
    url_field = "strava_url" if is_strava else "ridewithgps_url"
    url_value = payload.strava_url if is_strava else payload.ridewithgps_url

    try:
        ride = Ride.objects.get(**{id_field: payload.external_id})
        merged = False
    except Ride.DoesNotExist:
        ride = _find_cross_source_match(payload, id_field=id_field, other_id_field=other_id_field)
        merged = ride is not None
        if ride is None:
            if not create_if_unmatched:
                result.skipped += 1
                logger.info(
                    "Skipping %r (%s): no matching ride and create_if_unmatched=False",
                    payload.name, payload.external_id,
                )
                return
            ride = Ride(source=source)

    is_new = ride.pk is None

    if merged:
        # Only link the new source in and fill gaps — don't clobber data
        # already contributed by the source that created this row.
        setattr(ride, id_field, payload.external_id)
        setattr(ride, url_field, url_value)
        if not ride.start_city and payload.start_city:
            ride.start_city = payload.start_city
        if not ride.ride_date and payload.ride_date:
            ride.ride_date = payload.ride_date
        if len(payload.geometry) > len(ride.geometry or []):
            ride.geometry = payload.geometry
    else:
        ride.name = clean_ride_name(payload.name)
        ride.distance_m = payload.distance_m
        ride.elevation_gain_m = payload.elevation_gain_m
        ride.geometry = payload.geometry
        setattr(ride, id_field, payload.external_id)
        setattr(ride, url_field, url_value)
        if payload.ride_date:
            ride.ride_date = payload.ride_date
        if payload.start_city:
            ride.start_city = payload.start_city

    ride.save()

    if is_new:
        result.created += 1
        logger.info("Created ride %r (%s: %s)", ride.name, source, payload.external_id)
    elif merged:
        result.merged += 1
        logger.info("Merged %s route %s into existing ride %r", source, payload.external_id, ride.name)
    else:
        result.updated += 1
        logger.info("Updated ride %r (%s: %s)", ride.name, source, payload.external_id)

    if render_thumbnails and (is_new or merged or not ride.thumbnail):
        try:
            thumb = build_thumbnail_file(ride)
            if thumb is not None:
                ride.thumbnail.save(thumb.name, thumb, save=True)
                result.thumbnails += 1
                logger.debug("Rendered thumbnail for %r", ride.name)
        except Exception as exc:  # network/tile failures shouldn't abort the import
            result.errors.append(f"vignette {payload.external_id}: {exc}")
            logger.warning("Thumbnail failed for %r (%s): %s", ride.name, payload.external_id, exc)


def import_strava(
    *,
    render_thumbnails: bool = True,
    client: StravaClient | None = None,
    create_if_unmatched: bool = True,
    full: bool = False,
) -> ImportResult:
    """Import Strava routes (from the manually curated ``STRAVA_ROUTE_IDS``).

    Each route is matched onto an existing RideWithGPS-sourced ride by name +
    similar distance and merged in; pass ``create_if_unmatched=False`` to skip
    (rather than create standalone) routes with no match, if you only ever
    want Strava to enrich rides RideWithGPS already imported.
    """
    client = client or StravaClient()
    logger.info(
        "Starting Strava import (create_if_unmatched=%s, full=%s)",
        create_if_unmatched,
        full,
    )
    result = ImportResult()
    skip_route_ids = set() if full else set(
        Ride.objects.exclude(strava_activity_id="").values_list("strava_activity_id", flat=True)
    )
    for ride in client.fetch_rides(skip_route_ids=skip_route_ids):
        _upsert(
            Ride.Source.STRAVA,
            ride,
            id_field="strava_activity_id",
            other_id_field="rwgps_route_id",
            render_thumbnails=render_thumbnails,
            result=result,
            create_if_unmatched=create_if_unmatched,
        )
    logger.info("Strava import finished: %s", result)
    return result


def import_ridewithgps(
    *,
    render_thumbnails: bool = True,
    client: RideWithGPSClient | None = None,
    create_if_unmatched: bool = True,
    full: bool = False,
) -> ImportResult:
    """Import RideWithGPS routes — the primary, bulk source (see module docstring)."""
    client = client or RideWithGPSClient()
    logger.info(
        "Starting RideWithGPS import (create_if_unmatched=%s, full=%s)",
        create_if_unmatched,
        full,
    )
    result = ImportResult()
    skip_route_ids = set() if full else set(
        Ride.objects.exclude(rwgps_route_id="").values_list("rwgps_route_id", flat=True)
    )
    for ride in client.fetch_rides(skip_route_ids=skip_route_ids):
        _upsert(
            Ride.Source.RWGPS,
            ride,
            id_field="rwgps_route_id",
            other_id_field="strava_activity_id",
            render_thumbnails=render_thumbnails,
            result=result,
            create_if_unmatched=create_if_unmatched,
        )
    logger.info("RideWithGPS import finished: %s", result)
    return result

"""Minimal Strava API v3 client for importing an athlete's public routes.

Imports Strava *routes* (courses built in Strava's route planner), not
*activities* (recorded rides). Routes are what the club actually wants
published — the reusable courses, not individual members' logged rides.

Strava's route *listing* endpoint (``GET /athletes/{id}/routes``) only ever
returns routes for whichever athlete is currently authenticated — passing any
other athlete's id returns 403, even for routes that are public, and there's
no supported way around that for a third-party app (the mobile/web app can
browse another athlete's routes because it uses Strava's private, unpublished
API — not something available to OAuth apps, and not worth reverse-engineering
past its certificate pinning). So there is no way to bulk-list an arbitrary
athlete's routes here. This client instead fetches routes **by id**
(``GET /routes/{id}``, which does work for public routes regardless of who
authorized the token) from an explicit, manually curated list in
``STRAVA_ROUTE_IDS`` — see importer.py for why RideWithGPS, not Strava, is the
primary/bulk import source. ``STRAVA_ROUTE_IDS`` is entirely optional; leave
it empty if you don't want any Strava links at all.

Auth uses the refresh-token grant: given a long-lived refresh token, we mint a
short-lived access token per run. See the README for how to obtain the refresh
token once via the OAuth consent flow — any Strava account can authorize this
(the basic `read` scope is enough for public routes); it doesn't need to be
the account that created the routes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import requests
from django.conf import settings

from .geometry import decode_polyline
from .location import geometry_starts_in_quebec

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://www.strava.com/oauth/token"
_API = "https://www.strava.com/api/v3"
_TIMEOUT = 30

# Strava route objects expose an integer `type` rather than the newer
# `sport_type` field used by activities. In practice Strava returns 1 for
# regular ride routes and 6 for gravel ride routes.
_CYCLING_ROUTE_TYPES = {1, 6}


class StravaError(RuntimeError):
    pass


class StravaRouteFetchError(StravaError):
    def __init__(self, route_id, status_code: int, body: str):
        self.route_id = str(route_id)
        self.status_code = status_code
        self.body = body
        super().__init__(
            f"Route fetch failed for id {route_id} ({status_code}): {body}"
        )


@dataclass
class StravaRide:
    external_id: str
    name: str
    distance_m: float
    elevation_gain_m: float
    ride_date: date | None
    start_city: str
    geometry: list[list[float]]
    strava_url: str
    raw: dict = field(default_factory=dict)


class StravaClient:
    def __init__(self, client_id="", client_secret="", refresh_token="", route_ids=None):
        self.client_id = client_id or settings.STRAVA_CLIENT_ID
        self.client_secret = client_secret or settings.STRAVA_CLIENT_SECRET
        self.refresh_token = refresh_token or settings.STRAVA_REFRESH_TOKEN
        self.route_ids = list(route_ids) if route_ids is not None else list(settings.STRAVA_ROUTE_IDS)
        self._access_token: str | None = None

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def _access(self) -> str:
        if self._access_token:
            return self._access_token
        if not self.is_configured():
            raise StravaError(
                "Strava is not configured. Set STRAVA_CLIENT_ID, "
                "STRAVA_CLIENT_SECRET and STRAVA_REFRESH_TOKEN."
            )
        resp = requests.post(
            _TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.error("Strava token refresh failed (%s): %s", resp.status_code, resp.text)
            raise StravaError(f"Token refresh failed ({resp.status_code}): {resp.text}")
        self._access_token = resp.json()["access_token"]
        logger.debug("Strava access token refreshed")
        return self._access_token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._access()}"}

    def _get_route(self, route_id) -> dict:
        logger.debug("Fetching Strava route %s", route_id)
        resp = requests.get(
            f"{_API}/routes/{route_id}", headers=self._headers(), timeout=_TIMEOUT
        )
        if resp.status_code != 200:
            raise StravaRouteFetchError(route_id, resp.status_code, resp.text)
        return resp.json()

    @staticmethod
    def _is_public_cycling_route(route: dict) -> bool:
        if route.get("type") not in _CYCLING_ROUTE_TYPES:
            return False
        # Explicit safety net: only ever import public courses.
        return not route.get("private", False)

    def fetch_rides(self, *, skip_route_ids: set[str] | None = None) -> list[StravaRide]:
        # STRAVA_ROUTE_IDS is optional — Strava is a secondary, manually
        # curated enrichment source (see importer.py), not required for a
        # working import. Empty means "nothing to do", not an error.
        if not self.route_ids:
            logger.info("STRAVA_ROUTE_IDS is empty — nothing to fetch")
            return []
        skip_route_ids = skip_route_ids or set()
        logger.info("Fetching %d Strava route(s): %s", len(self.route_ids), self.route_ids)
        routes: list[StravaRide] = []
        for route_id in self.route_ids:
            if str(route_id) in skip_route_ids:
                logger.debug("Skipping Strava route %s: already imported", route_id)
                continue
            try:
                detail = self._get_route(route_id)
            except StravaRouteFetchError as exc:
                logger.error(
                    "Skipping Strava route %s: fetch failed (%s): %s",
                    exc.route_id,
                    exc.status_code,
                    exc.body,
                )
                continue
            if not self._is_public_cycling_route(detail):
                logger.info(
                    "Skipping Strava route %s: not a public cycling route (type=%r, private=%r)",
                    route_id,
                    detail.get("type"),
                    detail.get("private"),
                )
                continue
            ride = self._to_ride(detail)
            if not geometry_starts_in_quebec(ride.geometry):
                logger.info("Skipping Strava route %s: start is outside Quebec", route_id)
                continue
            routes.append(ride)
            logger.info(
                "Fetched Strava route %s: %s (%.1f km)",
                ride.external_id,
                ride.name,
                ride.distance_m / 1000.0,
            )
        return routes

    @staticmethod
    def _to_ride(route: dict) -> StravaRide:
        polyline = (route.get("map") or {}).get("polyline") or (route.get("map") or {}).get(
            "summary_polyline"
        ) or ""
        geometry = decode_polyline(polyline)
        return StravaRide(
            external_id=str(route.get("id")),
            name=route.get("name") or "Parcours Strava",
            distance_m=float(route.get("distance") or 0),
            elevation_gain_m=float(route.get("elevation_gain") or 0),
            # Strava only exposes when the *route* was created, not the day
            # the club actually rode it — left unset for the same reason as
            # RideWithGPS (see ridewithgps.py). Set manually in the admin if
            # known.
            ride_date=None,
            start_city="",
            geometry=geometry,
            strava_url=f"https://www.strava.com/routes/{route.get('id')}",
            raw=route,
        )

"""Minimal RideWithGPS API client for importing a user's routes.

RideWithGPS exposes routes under /users/{id}/routes.json. Full track points
come from the individual route endpoint (/routes/{id}.json). Auth is via an
API key plus (optionally) a user auth token for private routes. Extra routes
outside the configured user can be fetched directly by id via
``RWGPS_EXTRA_ROUTE_IDS``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import requests
from django.conf import settings

from .location import rwgps_route_starts_in_quebec

logger = logging.getLogger(__name__)

_API = "https://ridewithgps.com"
_TIMEOUT = 30


class RideWithGPSError(RuntimeError):
    pass


@dataclass
class RWGPSRide:
    external_id: str
    name: str
    distance_m: float
    elevation_gain_m: float
    ride_date: date | None
    start_city: str
    geometry: list[list[float]]
    ridewithgps_url: str
    raw: dict = field(default_factory=dict)


class RideWithGPSClient:
    def __init__(self, api_key="", auth_token="", user_id="", extra_route_ids=None):
        self.api_key = api_key or settings.RWGPS_API_KEY
        self.auth_token = auth_token or settings.RWGPS_AUTH_TOKEN
        self.user_id = str(user_id or settings.RWGPS_USER_ID)
        self.extra_route_ids = (
            list(extra_route_ids)
            if extra_route_ids is not None
            else list(settings.RWGPS_EXTRA_ROUTE_IDS)
        )

    def is_configured(self) -> bool:
        return bool(self.api_key and (self.user_id or self.extra_route_ids))

    def _params(self, **extra) -> dict:
        params = {"version": 2}
        if self.api_key:
            params["apikey"] = self.api_key
        if self.auth_token:
            params["auth_token"] = self.auth_token
        params.update(extra)
        return params

    def _get(self, path: str, **params) -> dict:
        resp = requests.get(f"{_API}{path}", params=self._params(**params), timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.error("RideWithGPS GET %s failed (%s): %s", path, resp.status_code, resp.text)
            raise RideWithGPSError(f"GET {path} failed ({resp.status_code}): {resp.text}")
        return resp.json()

    def _get_route_detail(self, route_id: str, *, fallback: dict | None = None) -> dict:
        detail = self._get(f"/routes/{route_id}.json").get("route") or fallback or {}
        if detail.get("id") is None:
            detail = {**detail, "id": route_id}
        return detail

    def iter_route_summaries(self, per_page: int = 50, max_pages: int = 20):
        if not (self.api_key and self.user_id):
            raise RideWithGPSError(
                "RideWithGPS is not configured. Set RWGPS_API_KEY and RWGPS_USER_ID."
            )
        for page in range(max_pages):
            offset = page * per_page
            logger.debug("Fetching RideWithGPS routes page %d (offset=%d)", page + 1, offset)
            data = self._get(
                f"/users/{self.user_id}/routes.json", offset=offset, limit=per_page
            )
            results = data.get("results") or data.get("routes") or []
            if not results:
                return
            yield from results

    def _fetch_ride(self, route_id: str, *, fallback: dict | None = None) -> RWGPSRide | None:
        detail = self._get_route_detail(route_id, fallback=fallback)
        if not self._is_cycling(detail):
            logger.info("Skipping RideWithGPS route %s: not cycling", route_id)
            return None
        if not rwgps_route_starts_in_quebec(detail):
            logger.info("Skipping RideWithGPS route %s: start is outside Quebec", route_id)
            return None
        ride = self._to_ride(detail)
        logger.info(
            "Fetched RideWithGPS route %s: %s (%.1f km)",
            ride.external_id,
            ride.name,
            ride.distance_m / 1000.0,
        )
        return ride

    def fetch_rides(self, *, skip_route_ids: set[str] | None = None) -> list[RWGPSRide]:
        if not self.is_configured():
            raise RideWithGPSError(
                "RideWithGPS is not configured. Set RWGPS_API_KEY and RWGPS_USER_ID "
                "or RWGPS_EXTRA_ROUTE_IDS."
            )
        skip_route_ids = {str(route_id) for route_id in (skip_route_ids or set())}
        rides: list[RWGPSRide] = []
        count = 0
        seen_route_ids: set[str] = set()

        if self.user_id:
            for summary in self.iter_route_summaries():
                count += 1
                route_id = summary.get("id")
                if route_id is None:
                    continue
                route_id = str(route_id)
                if route_id in seen_route_ids:
                    logger.debug("Skipping RideWithGPS route %s: duplicate in user listing", route_id)
                    continue
                seen_route_ids.add(route_id)
                if route_id in settings.RWGPS_EXCLUDED_ROUTE_IDS:
                    logger.info("Skipping RideWithGPS route %s: excluded by configuration", route_id)
                    continue
                if route_id in skip_route_ids:
                    logger.debug("Skipping RideWithGPS route %s: already imported", route_id)
                    continue
                ride = self._fetch_ride(route_id, fallback=summary)
                if ride is not None:
                    rides.append(ride)

        for route_id in self.extra_route_ids:
            route_id = str(route_id).strip()
            if not route_id:
                continue
            if route_id in seen_route_ids:
                logger.debug("Skipping RideWithGPS route %s: duplicate extra route id", route_id)
                continue
            seen_route_ids.add(route_id)
            if route_id in settings.RWGPS_EXCLUDED_ROUTE_IDS:
                logger.info("Skipping RideWithGPS route %s: excluded by configuration", route_id)
                continue
            if route_id in skip_route_ids:
                logger.debug("Skipping RideWithGPS route %s: already imported", route_id)
                continue
            ride = self._fetch_ride(route_id)
            if ride is not None:
                rides.append(ride)

        logger.info(
            "Fetched %d RideWithGPS route(s), %d configured extra route id(s), %d after filters",
            count,
            len(self.extra_route_ids),
            len(rides),
        )
        return rides

    @staticmethod
    def _is_cycling(route: dict) -> bool:
        """RideWithGPS route detail responses carry an ``activity_types``
        array (up to 3 values) such as ``cycling:road``, ``cycling:gravel``,
        ``walking:hiking``, ``running:generic``, ``motorcycling:generic``.
        Missing/empty is treated as cycling (older routes may predate the
        field); anything explicitly tagged should be a cycling:* type.
        """
        types = route.get("activity_types") or []
        if not types:
            return True
        return any(str(t).startswith("cycling:") for t in types)

    @staticmethod
    def _to_ride(route: dict) -> RWGPSRide:
        track = route.get("track_points") or []
        geometry = [
            [pt["y"], pt["x"]]
            for pt in track
            if pt.get("y") is not None and pt.get("x") is not None
        ]
        # RideWithGPS only exposes the *route's* created_at, i.e. when the
        # course was designed/uploaded — not the day the club actually rode
        # it. Deliberately left unset here rather than passed off as the ride
        # date; Strava's activity date (the real ride day) fills it in when
        # the two get matched, and it can otherwise be set manually.
        return RWGPSRide(
            external_id=str(route.get("id")),
            name=route.get("name") or "Parcours RideWithGPS",
            distance_m=float(route.get("distance") or 0),
            elevation_gain_m=float(route.get("elevation_gain") or 0),
            ride_date=None,
            start_city=route.get("locality") or route.get("administrative_area") or "",
            geometry=geometry,
            ridewithgps_url=f"https://ridewithgps.com/routes/{route.get('id')}",
            raw=route,
        )

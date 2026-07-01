"""Minimal RideWithGPS API client for importing a user's routes.

RideWithGPS exposes routes under /users/{id}/routes.json. Full track points
come from the individual route endpoint (/routes/{id}.json). Auth is via an
API key plus (optionally) a user auth token for private routes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import requests
from django.conf import settings

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
    def __init__(self, api_key="", auth_token="", user_id=""):
        self.api_key = api_key or settings.RWGPS_API_KEY
        self.auth_token = auth_token or settings.RWGPS_AUTH_TOKEN
        self.user_id = str(user_id or settings.RWGPS_USER_ID)

    def is_configured(self) -> bool:
        return bool(self.api_key and self.user_id)

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

    def iter_route_summaries(self, per_page: int = 50, max_pages: int = 20):
        if not self.is_configured():
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

    def fetch_rides(self) -> list[RWGPSRide]:
        rides: list[RWGPSRide] = []
        count = 0
        for summary in self.iter_route_summaries():
            count += 1
            route_id = summary.get("id")
            if route_id is None:
                continue
            detail = self._get(f"/routes/{route_id}.json").get("route", summary)
            if not self._is_cycling(detail):
                logger.info("Skipping RideWithGPS route %s: not cycling", route_id)
                continue
            rides.append(self._to_ride(detail))
        logger.info("Fetched %d RideWithGPS route(s), %d after cycling filter", count, len(rides))
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

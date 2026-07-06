import xml.etree.ElementTree as ET
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from rides.models import Ride
from rides.services import importer
from rides.services.geometry import bounds, decode_polyline, downsample
from rides.services.location import (
    administrative_area_is_quebec,
    geometry_starts_in_quebec,
    infer_start_city,
)
from rides.services.ravitos import (
    Ravito,
    find_nearby_parking,
    find_nearby_ravitos,
    parse_parking_points,
    parse_ravito_points,
)
from rides.services.ridewithgps import RideWithGPSClient, RWGPSRide
from rides.services.strava import StravaClient, StravaRide, StravaRouteFetchError


SQUARE = [[45.0, -72.0], [45.1, -72.0], [45.1, -71.9], [45.0, -71.9], [45.0, -72.0]]
QUEBEC_POLYLINE = "_atqG~nmvL_pR?"
VIRGINIA_POLYLINE = "_f{mFzbjyM_`K{iB"


class GeometryTests(TestCase):
    def test_decode_polyline_roundtrip(self):
        # A known Google-encoded polyline.
        points = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        self.assertEqual(len(points), 3)
        self.assertAlmostEqual(points[0][0], 38.5, places=1)

    def test_downsample_keeps_endpoints(self):
        pts = [[float(i), 0.0] for i in range(1000)]
        out = downsample(pts, max_points=100)
        self.assertLessEqual(len(out), 100)
        self.assertEqual(out[0], pts[0])
        self.assertEqual(out[-1], pts[-1])

    def test_bounds(self):
        self.assertEqual(bounds(SQUARE), (45.0, -72.0, 45.1, -71.9))
        self.assertIsNone(bounds([]))


class RavitoTests(TestCase):
    def test_parse_ravito_points(self):
        raw = "Epicerie du Coin|45.1|-72.2; Depanneur Test | 45.2 | -72.3 "
        ravitos = parse_ravito_points(raw)
        self.assertEqual([r.name for r in ravitos], ["Epicerie du Coin", "Depanneur Test"])
        self.assertEqual(ravitos[0].lat, 45.1)
        self.assertEqual(ravitos[0].lng, -72.2)

    def test_parse_ravito_points_ignores_invalid_entries(self):
        raw = "Incomplete|45.1;Bad coords|nope|-72;Out|95|-72;Good|45|-72"
        ravitos = parse_ravito_points(raw)
        self.assertEqual([r.name for r in ravitos], ["Good"])

    def test_parse_ravito_points_accepts_full_google_maps_urls(self):
        url = (
            "https://www.google.com/maps/place/Epicerie+Route/"
            "@45.0004,-71.995,17z/data=!4m6!3m5!8m2!3d45.0004!4d-71.995"
        )
        with mock.patch("rides.services.ravitos._resolve_url") as resolve:
            ravitos = parse_ravito_points(url)

        resolve.assert_not_called()
        self.assertEqual(len(ravitos), 1)
        self.assertEqual(ravitos[0].name, "Epicerie Route")
        self.assertEqual(ravitos[0].lat, 45.0004)
        self.assertEqual(ravitos[0].lng, -71.995)
        self.assertEqual(ravitos[0].url, url)

    def test_parse_ravito_points_resolves_short_google_maps_urls(self):
        short_url = "https://maps.app.goo.gl/example"
        resolved_url = (
            "https://www.google.com/maps/place/Cafe+Test/"
            "@45.0004,-71.995,17z/data=!4m6!3m5!8m2!3d45.0004!4d-71.995"
        )
        with mock.patch("rides.services.ravitos._resolve_url", return_value=resolved_url):
            ravitos = parse_ravito_points(short_url)

        self.assertEqual(len(ravitos), 1)
        self.assertEqual(ravitos[0].name, "Cafe Test")
        self.assertEqual(ravitos[0].url, short_url)

    def test_parse_ravito_points_accepts_comma_separated_urls(self):
        raw = "Cafe A|https://maps.app.goo.gl/a, Epicerie B|https://maps.app.goo.gl/b"
        resolved_urls = {
            "https://maps.app.goo.gl/a": (
                "https://www.google.com/maps/place/Cafe+A/"
                "@45.0004,-71.995,17z/data=!4m6!3m5!8m2!3d45.0004!4d-71.995"
            ),
            "https://maps.app.goo.gl/b": (
                "https://www.google.com/maps/place/Epicerie+B/"
                "@45.002,-71.996,17z/data=!4m6!3m5!8m2!3d45.002!4d-71.996"
            ),
        }

        with mock.patch("rides.services.ravitos._resolve_url", side_effect=resolved_urls.get):
            ravitos = parse_ravito_points(raw)

        self.assertEqual([r.name for r in ravitos], ["Cafe A", "Epicerie B"])

    def test_parse_ravito_points_supports_name_override_for_urls(self):
        url = "https://www.google.com/maps/place/Long+Google+Name/@45.0004,-71.995,17z"
        ravitos = parse_ravito_points(f"Ravito court|{url}")
        self.assertEqual([r.name for r in ravitos], ["Ravito court"])

    def test_parse_parking_points_uses_parking_default_name(self):
        parkings = parse_parking_points("https://www.google.com/maps/search/?api=1&query=45,-72")
        self.assertEqual(len(parkings), 1)
        self.assertEqual(parkings[0].name, "Stationnement")

    def test_parse_parking_points_accepts_google_search_path_coordinates(self):
        url = "https://www.google.com/maps/search/45.167415,+-72.038035?entry=tts"
        parkings = parse_parking_points(url)
        self.assertEqual(len(parkings), 1)
        self.assertEqual(parkings[0].lat, 45.167415)
        self.assertEqual(parkings[0].lng, -72.038035)

    def test_parse_map_points_dedupes_duplicate_urls(self):
        raw = "https://www.google.com/maps/search/?api=1&query=45,-72;Parking|https://www.google.com/maps/search/?api=1&query=45,-72"
        parkings = parse_parking_points(raw)
        self.assertEqual([parking.name for parking in parkings], ["Parking"])

    def test_find_nearby_ravitos_matches_route_segments_and_sorts_by_distance(self):
        route = [[45.0, -72.0], [45.0, -71.99]]
        ravitos = [
            Ravito("Far", 45.01, -72.0),
            Ravito("Closer", 45.0004, -71.995),
            Ravito("Close", 45.001, -71.995),
        ]

        matches = find_nearby_ravitos(route, ravitos, radius_m=500)

        self.assertEqual([match.ravito.name for match in matches], ["Closer", "Close"])
        self.assertLess(matches[0].distance_m, matches[1].distance_m)
        self.assertLess(matches[0].distance_m, 100)

    def test_find_nearby_ravitos_filters_stops_too_close_to_route_ends(self):
        route = [[45.0, -72.0], [45.0, -71.0]]
        ravitos = [
            Ravito("Too early", 45.0, -71.95),
            Ravito("Relevant", 45.0, -71.5),
            Ravito("Too late", 45.0, -71.05),
        ]

        matches = find_nearby_ravitos(
            route,
            ravitos,
            radius_m=500,
            min_route_distance_m=30_000,
            endpoint_exclusion_radius_m=5_000,
        )

        self.assertEqual([match.ravito.name for match in matches], ["Relevant"])
        self.assertGreaterEqual(matches[0].route_distance_m, 30_000)

    def test_find_nearby_parking_matches_only_route_start(self):
        route = [[45.0, -72.0], [45.0, -71.0]]
        parkings = [
            Ravito("Depart", 45.0002, -72.0),
            Ravito("Arrivee", 45.0, -71.0),
        ]

        matches = find_nearby_parking(route, parkings, radius_m=500)

        self.assertEqual([match.parking.name for match in matches], ["Depart"])
        self.assertLess(matches[0].distance_m, 50)


class QuebecLocationFilterTests(TestCase):
    def test_administrative_area_accepts_quebec_variants(self):
        self.assertTrue(administrative_area_is_quebec("Quebec"))
        self.assertTrue(administrative_area_is_quebec("Québec"))
        self.assertTrue(administrative_area_is_quebec("QC"))
        self.assertFalse(administrative_area_is_quebec("Virginia"))

    def test_geometry_start_must_be_in_quebec(self):
        self.assertTrue(geometry_starts_in_quebec([[45.4, -71.9], [45.5, -71.8]]))
        self.assertFalse(geometry_starts_in_quebec([[39.1384, -77.7171], [39.2, -77.7]]))
        self.assertFalse(geometry_starts_in_quebec([]))

    def test_infer_start_city_from_known_departure_hubs(self):
        self.assertEqual(infer_start_city([[45.22231, -72.53192]]), "Lac-Brome")
        self.assertEqual(infer_start_city([[45.33559, -72.51204]]), "Waterloo")
        self.assertEqual(infer_start_city([[47.89885, -69.32799]]), "Rivière-du-Loup")
        self.assertEqual(infer_start_city([[45.0, -72.0]]), "")


class RideModelTests(TestCase):
    def test_unique_slug_generated(self):
        a = Ride.objects.create(name="Boucle du lac")
        b = Ride.objects.create(name="Boucle du lac")
        self.assertNotEqual(a.slug, b.slug)

    def test_derived_stats(self):
        r = Ride.objects.create(name="X", distance_m=48500, elevation_gain_m=1119.6)
        self.assertEqual(r.distance_km, 48.5)
        self.assertEqual(r.elevation_m, 1120)


class StravaRoutesFilterTests(TestCase):
    def _client(self, route_ids):
        return StravaClient(client_id="x", client_secret="y", refresh_token="z", route_ids=route_ids)

    def test_fetch_rides_keeps_only_public_cycling_type_routes(self):
        client = self._client(route_ids=["1", "2", "3", "4"])
        details = {
            "1": {"id": 1, "type": 1, "private": False, "name": "A", "distance": 1000,
                  "elevation_gain": 10, "map": {"polyline": QUEBEC_POLYLINE}},
            "2": {"id": 2, "type": 2, "private": False, "name": "B", "distance": 1000,
                  "elevation_gain": 10, "map": {"polyline": QUEBEC_POLYLINE}},  # run -> excluded
            "3": {"id": 3, "type": 1, "private": True, "name": "C", "distance": 1000,
                  "elevation_gain": 10, "map": {"polyline": QUEBEC_POLYLINE}},  # private -> excluded
            "4": {"id": 4, "type": 6, "private": False, "name": "D", "distance": 1000,
                  "elevation_gain": 10, "map": {"polyline": QUEBEC_POLYLINE}},
        }
        with mock.patch.object(StravaClient, "_get_route", side_effect=lambda rid: details[rid]):
            rides = client.fetch_rides()
        self.assertEqual({r.external_id for r in rides}, {"1", "4"})

    def test_route_urls_and_no_ride_date(self):
        client = self._client(route_ids=["42"])
        details = {"42": {"id": 42, "type": 1, "private": False, "name": "Boucle",
                           "distance": 5000, "elevation_gain": 100,
                           "map": {"polyline": QUEBEC_POLYLINE}}}
        with mock.patch.object(StravaClient, "_get_route", side_effect=lambda rid: details[rid]):
            rides = client.fetch_rides()
        self.assertEqual(len(rides), 1)
        self.assertEqual(rides[0].strava_url, "https://www.strava.com/routes/42")
        self.assertIsNone(rides[0].ride_date)

    def test_strava_routes_infer_start_city_from_geometry(self):
        client = self._client(route_ids=["42"])
        details = {"42": {"id": 42, "type": 1, "private": False, "name": "Boucle",
                           "distance": 5000, "elevation_gain": 100,
                           "map": {"polyline": "mn_sGnkuyLAC"}}}
        with mock.patch.object(StravaClient, "_get_route", side_effect=lambda rid: details[rid]):
            rides = client.fetch_rides()

        self.assertEqual(rides[0].start_city, "Lac-Brome")


    def test_fetch_rides_returns_empty_without_route_ids_configured(self):
        # STRAVA_ROUTE_IDS is optional (Strava is a secondary source) — an
        # empty list means "nothing to do", not an error.
        client = self._client(route_ids=[])
        self.assertEqual(client.fetch_rides(), [])

    def test_fetch_rides_skips_existing_route_ids_before_api_fetch(self):
        client = self._client(route_ids=["41", "42", "42"])
        details = {
            "41": {"id": 41, "type": 1, "private": False, "name": "A", "distance": 1000,
                   "elevation_gain": 10, "map": {"polyline": QUEBEC_POLYLINE}},
        }

        with mock.patch.object(StravaClient, "_get_route", side_effect=lambda rid: details[rid]) as get_route, \
             self.assertLogs("rides.services.strava", level="INFO") as logs:
            rides = client.fetch_rides(skip_route_ids={"42"})

        self.assertEqual({r.external_id for r in rides}, {"41"})
        get_route.assert_called_once_with("41")
        output = "\n".join(logs.output)
        self.assertIn("Skipping 1 already-imported Strava route(s) before API fetch", output)
        self.assertIn("Skipping 1 duplicate Strava route id(s) before API fetch", output)

    def test_fetch_rides_returns_empty_when_all_route_ids_already_imported(self):
        client = self._client(route_ids=["42"])
        with mock.patch.object(StravaClient, "_get_route") as get_route, \
             self.assertLogs("rides.services.strava", level="INFO") as logs:
            rides = client.fetch_rides(skip_route_ids={"42"})

        self.assertEqual(rides, [])
        get_route.assert_not_called()
        self.assertIn("No Strava route API calls needed", "\n".join(logs.output))

    def test_fetch_rides_skips_routes_outside_quebec(self):
        client = self._client(route_ids=["1", "2"])
        details = {
            "1": {"id": 1, "type": 1, "private": False, "name": "A", "distance": 1000,
                  "elevation_gain": 10, "map": {"polyline": QUEBEC_POLYLINE}},
            "2": {"id": 2, "type": 1, "private": False, "name": "B", "distance": 1000,
                  "elevation_gain": 10, "map": {"polyline": VIRGINIA_POLYLINE}},
        }
        with mock.patch.object(StravaClient, "_get_route", side_effect=lambda rid: details[rid]):
            rides = client.fetch_rides()
        self.assertEqual({r.external_id for r in rides}, {"1"})

    def test_fetch_rides_continues_after_route_fetch_error(self):
        client = self._client(route_ids=["1", "missing", "2"])
        details = {
            "1": {"id": 1, "type": 1, "private": False, "name": "A", "distance": 1000,
                  "elevation_gain": 10, "map": {"polyline": QUEBEC_POLYLINE}},
            "2": {"id": 2, "type": 1, "private": False, "name": "B", "distance": 2000,
                  "elevation_gain": 20, "map": {"polyline": QUEBEC_POLYLINE}},
        }

        def fake_get_route(route_id):
            if route_id == "missing":
                raise StravaRouteFetchError(route_id, 404, '{"message":"Resource Not Found"}')
            return details[route_id]

        with mock.patch.object(StravaClient, "_get_route", side_effect=fake_get_route), \
             self.assertLogs("rides.services.strava", level="ERROR") as logs:
            rides = client.fetch_rides()

        self.assertEqual({r.external_id for r in rides}, {"1", "2"})
        self.assertIn("Skipping Strava route missing: fetch failed (404)", "\n".join(logs.output))


@override_settings(RWGPS_EXTRA_ROUTE_IDS=[])
class RideWithGPSCyclingFilterTests(TestCase):
    def test_is_cycling_accepts_cycling_prefixed_types(self):
        is_cycling = RideWithGPSClient._is_cycling
        self.assertTrue(is_cycling({"activity_types": ["cycling:road"]}))
        self.assertTrue(is_cycling({"activity_types": ["cycling:gravel", "cycling:mountain"]}))
        self.assertFalse(is_cycling({"activity_types": ["walking:hiking"]}))
        self.assertFalse(is_cycling({"activity_types": ["running:generic"]}))
        self.assertFalse(is_cycling({"activity_types": ["motorcycling:generic"]}))

    def test_is_cycling_defaults_true_when_missing(self):
        is_cycling = RideWithGPSClient._is_cycling
        self.assertTrue(is_cycling({}))
        self.assertTrue(is_cycling({"activity_types": []}))

    def test_fetch_rides_skips_non_cycling_routes(self):
        client = RideWithGPSClient(api_key="k", user_id="1")
        summaries = [{"id": 10}, {"id": 20}]
        details = {
            10: {"route": {"id": 10, "name": "Gravel loop", "distance": 1000,
                            "activity_types": ["cycling:gravel"], "administrative_area": "Quebec",
                            "track_points": []}},
            20: {"route": {"id": 20, "name": "Nature hike", "distance": 1000,
                            "activity_types": ["walking:hiking"], "track_points": []}},
        }
        with mock.patch.object(RideWithGPSClient, "iter_route_summaries", return_value=summaries), \
             mock.patch.object(RideWithGPSClient, "_get", side_effect=lambda path, **p: details[int(path.split("/")[-1].split(".")[0])]):
            rides = client.fetch_rides()
        self.assertEqual({r.external_id for r in rides}, {"10"})

    def test_fetch_rides_skips_existing_route_ids_before_detail_fetch(self):
        client = RideWithGPSClient(api_key="k", user_id="1")
        summaries = [{"id": 10}, {"id": 20}]
        details = {
            10: {"route": {"id": 10, "name": "Gravel loop", "distance": 1000,
                            "activity_types": ["cycling:gravel"], "administrative_area": "Quebec",
                            "track_points": []}},
        }
        with mock.patch.object(RideWithGPSClient, "iter_route_summaries", return_value=summaries), \
             mock.patch.object(RideWithGPSClient, "_get", side_effect=lambda path, **p: details[int(path.split("/")[-1].split(".")[0])]) as get_detail:
            rides = client.fetch_rides(skip_route_ids={"20"})
        self.assertEqual({r.external_id for r in rides}, {"10"})
        self.assertEqual(get_detail.call_count, 1)

    @override_settings(RWGPS_EXTRA_ROUTE_IDS=["30"], RWGPS_EXCLUDED_ROUTE_IDS=[])
    def test_fetch_rides_includes_extra_route_ids(self):
        client = RideWithGPSClient(api_key="k", user_id="1")
        summaries = [{"id": 10}]
        details = {
            "10": {"route": {"id": 10, "name": "User route", "distance": 1000,
                              "activity_types": ["cycling:gravel"],
                              "administrative_area": "Quebec", "track_points": []}},
            "30": {"route": {"id": 30, "name": "Extra route", "distance": 2000,
                              "activity_types": ["cycling:road"],
                              "administrative_area": "Quebec", "track_points": []}},
        }

        def fake_get(path, **_params):
            route_id = path.split("/")[-1].split(".")[0]
            return details[route_id]

        with mock.patch.object(RideWithGPSClient, "iter_route_summaries", return_value=summaries), \
             mock.patch.object(RideWithGPSClient, "_get", side_effect=fake_get):
            rides = client.fetch_rides()

        self.assertEqual({r.external_id for r in rides}, {"10", "30"})

    @override_settings(RWGPS_EXTRA_ROUTE_IDS=["10", "30"], RWGPS_EXCLUDED_ROUTE_IDS=[])
    def test_fetch_rides_does_not_fetch_extra_route_ids_already_seen_in_user_routes(self):
        client = RideWithGPSClient(api_key="k", user_id="1")
        summaries = [{"id": 10}]
        details = {
            "10": {"route": {"id": 10, "name": "User route", "distance": 1000,
                              "activity_types": ["cycling:gravel"],
                              "administrative_area": "Quebec", "track_points": []}},
            "30": {"route": {"id": 30, "name": "Extra route", "distance": 2000,
                              "activity_types": ["cycling:road"],
                              "administrative_area": "Quebec", "track_points": []}},
        }

        def fake_get(path, **_params):
            route_id = path.split("/")[-1].split(".")[0]
            return details[route_id]

        with mock.patch.object(RideWithGPSClient, "iter_route_summaries", return_value=summaries), \
             mock.patch.object(RideWithGPSClient, "_get", side_effect=fake_get) as get_detail:
            rides = client.fetch_rides()

        self.assertEqual({r.external_id for r in rides}, {"10", "30"})
        self.assertEqual(get_detail.call_count, 2)

    @override_settings(RWGPS_EXTRA_ROUTE_IDS=["30"], RWGPS_EXCLUDED_ROUTE_IDS=[])
    def test_fetch_rides_skips_existing_extra_route_ids_before_detail_fetch(self):
        client = RideWithGPSClient(api_key="k", user_id="1")
        with mock.patch.object(RideWithGPSClient, "iter_route_summaries", return_value=[]), \
             mock.patch.object(RideWithGPSClient, "_get") as get_detail:
            rides = client.fetch_rides(skip_route_ids={"30"})

        self.assertEqual(rides, [])
        get_detail.assert_not_called()

    @override_settings(RWGPS_EXCLUDED_ROUTE_IDS=["20"])
    def test_fetch_rides_skips_excluded_route_ids(self):
        client = RideWithGPSClient(api_key="k", user_id="1")
        summaries = [{"id": 10}, {"id": 20}]
        details = {
            10: {"route": {"id": 10, "name": "Gravel loop", "distance": 1000,
                            "activity_types": ["cycling:gravel"], "administrative_area": "Quebec",
                            "track_points": []}},
        }
        with mock.patch.object(RideWithGPSClient, "iter_route_summaries", return_value=summaries), \
             mock.patch.object(RideWithGPSClient, "_get", side_effect=lambda path, **p: details[int(path.split("/")[-1].split(".")[0])]):
            rides = client.fetch_rides()
        self.assertEqual({r.external_id for r in rides}, {"10"})

    def test_fetch_rides_skips_routes_outside_quebec(self):
        client = RideWithGPSClient(api_key="k", user_id="1")
        summaries = [{"id": 10}, {"id": 20}]
        details = {
            10: {"route": {"id": 10, "name": "Gravel loop", "distance": 1000,
                            "activity_types": ["cycling:gravel"], "administrative_area": "Quebec",
                            "track_points": []}},
            20: {"route": {"id": 20, "name": "Virginia loop", "distance": 1000,
                            "activity_types": ["cycling:gravel"], "administrative_area": "Virginia",
                            "track_points": [{"y": 39.1384, "x": -77.7171}]}},
        }
        with mock.patch.object(RideWithGPSClient, "iter_route_summaries", return_value=summaries), \
             mock.patch.object(RideWithGPSClient, "_get", side_effect=lambda path, **p: details[int(path.split("/")[-1].split(".")[0])]):
            rides = client.fetch_rides()
        self.assertEqual({r.external_id for r in rides}, {"10"})

    def test_fetch_rides_accepts_quebec_start_point_when_area_missing(self):
        client = RideWithGPSClient(api_key="k", user_id="1")
        summaries = [{"id": 10}]
        details = {
            10: {"route": {"id": 10, "name": "Gravel loop", "distance": 1000,
                            "activity_types": ["cycling:gravel"],
                            "track_points": [{"y": 45.4, "x": -71.9}, {"y": 45.5, "x": -71.8}]}},
        }
        with mock.patch.object(RideWithGPSClient, "iter_route_summaries", return_value=summaries), \
             mock.patch.object(RideWithGPSClient, "_get", side_effect=lambda path, **p: details[int(path.split("/")[-1].split(".")[0])]):
            rides = client.fetch_rides()
        self.assertEqual({r.external_id for r in rides}, {"10"})


class ImporterTests(TestCase):
    def _strava_payload(self, ext_id="1", **over):
        data = dict(
            external_id=ext_id, name="Sortie", distance_m=1000, elevation_gain_m=50,
            ride_date=None, start_city="Magog", geometry=SQUARE,
            strava_url="https://strava.com/activities/1", raw={},
        )
        data.update(over)
        return StravaRide(**data)

    def _rwgps_payload(self, ext_id="9", **over):
        data = dict(
            external_id=ext_id, name="Sortie", distance_m=1000, elevation_gain_m=50,
            ride_date=None, start_city="Magog", geometry=SQUARE,
            ridewithgps_url="https://ridewithgps.com/routes/9", raw={},
        )
        data.update(over)
        return RWGPSRide(**data)

    @mock.patch("rides.services.importer.build_thumbnail_file", return_value=None)
    def test_full_import_creates_then_updates(self, _thumb):
        client = mock.Mock(spec=StravaClient)
        client.fetch_rides.return_value = [self._strava_payload(distance_m=1000)]
        res = importer.import_strava(client=client)
        self.assertEqual((res.created, res.updated, res.merged), (1, 0, 0))

        client.fetch_rides.return_value = [self._strava_payload(distance_m=2000)]
        res2 = importer.import_strava(client=client, full=True)
        self.assertEqual((res2.created, res2.updated, res2.merged), (0, 1, 0))
        self.assertEqual(Ride.objects.get(strava_activity_id="1").distance_m, 2000)

    def test_rwgps_import_skips_existing_ids_by_default(self):
        Ride.objects.create(name="Existing", rwgps_route_id="9")
        client = mock.Mock(spec=RideWithGPSClient)
        client.fetch_rides.return_value = []

        importer.import_ridewithgps(client=client)

        client.fetch_rides.assert_called_once_with(skip_route_ids={"9"})

    def test_rwgps_full_import_does_not_skip_existing_ids(self):
        Ride.objects.create(name="Existing", rwgps_route_id="9")
        client = mock.Mock(spec=RideWithGPSClient)
        client.fetch_rides.return_value = []

        importer.import_ridewithgps(client=client, full=True)

        client.fetch_rides.assert_called_once_with(skip_route_ids=set())

    def test_strava_import_skips_existing_ids_by_default(self):
        Ride.objects.create(name="Existing", strava_activity_id="1")
        client = mock.Mock(spec=StravaClient)
        client.fetch_rides.return_value = []

        importer.import_strava(client=client)

        client.fetch_rides.assert_called_once_with(skip_route_ids={"1"})

    @mock.patch("rides.services.importer.build_thumbnail_file", return_value=None)
    def test_import_skips_rides_without_geometry(self, _thumb):
        client = mock.Mock(spec=StravaClient)
        client.fetch_rides.return_value = [self._strava_payload(geometry=[])]
        res = importer.import_strava(client=client)
        self.assertEqual(res.skipped, 1)
        self.assertEqual(Ride.objects.count(), 0)

    @mock.patch("rides.services.importer.build_thumbnail_file", return_value=None)
    def test_same_ride_from_both_sources_merges_into_one_row(self, _thumb):
        from datetime import date

        # Normal pipeline order: RideWithGPS (bulk/primary) first...
        rwgps_client = mock.Mock(spec=RideWithGPSClient)
        rwgps_client.fetch_rides.return_value = [
            self._rwgps_payload(
                name="Boucle du Lac Memphrémagog", distance_m=51000, start_city="Magog",
            )
        ]
        res1 = importer.import_ridewithgps(client=rwgps_client)
        self.assertEqual((res1.created, res1.merged), (1, 0))

        # ...then Strava (secondary, manually curated) matches onto it.
        actual_ride_day = date(2026, 6, 20)
        strava_client = mock.Mock(spec=StravaClient)
        # Same name, distance measured slightly differently.
        strava_client.fetch_rides.return_value = [
            self._strava_payload(
                name="Boucle du Lac Memphrémagog", ride_date=actual_ride_day,
                distance_m=50000, start_city="",
            )
        ]
        res2 = importer.import_strava(client=strava_client)
        self.assertEqual((res2.created, res2.merged), (0, 1))

        self.assertEqual(Ride.objects.count(), 1)
        ride = Ride.objects.get()
        self.assertTrue(ride.is_cross_linked)
        self.assertEqual(ride.strava_activity_id, "1")
        self.assertEqual(ride.rwgps_route_id, "9")
        # Enriched from the second source: distance/city from RWGPS untouched,
        # and the real ride date (from Strava) is backfilled by the merge.
        self.assertEqual(ride.start_city, "Magog")
        self.assertEqual(ride.distance_m, 51000)
        self.assertEqual(ride.ride_date, actual_ride_day)

    @mock.patch("rides.services.importer.build_thumbnail_file", return_value=None)
    def test_rwgps_creates_standalone_rides_by_default(self, _thumb):
        rwgps_client = mock.Mock(spec=RideWithGPSClient)
        rwgps_client.fetch_rides.return_value = [
            self._rwgps_payload(name="Gravelle du Mont-Orford", distance_m=48000)
        ]
        # No Strava ride to match against, but RWGPS is the primary/bulk
        # source now, so it creates a standalone ride by default.
        res = importer.import_ridewithgps(client=rwgps_client)
        self.assertEqual(res.created, 1)
        self.assertEqual(Ride.objects.count(), 1)

    @mock.patch("rides.services.importer.build_thumbnail_file", return_value=None)
    def test_strava_matches_onto_existing_rwgps_ride_by_name_and_distance(self, _thumb):
        from datetime import date

        rwgps_client = mock.Mock(spec=RideWithGPSClient)
        rwgps_client.fetch_rides.return_value = [
            self._rwgps_payload(name="Sortie du club", distance_m=30000)
        ]
        importer.import_ridewithgps(client=rwgps_client)

        actual_ride_day = date(2026, 6, 21)
        strava_client = mock.Mock(spec=StravaClient)
        strava_client.fetch_rides.return_value = [
            self._strava_payload(name="Sortie du club", ride_date=actual_ride_day, distance_m=30200)
        ]
        res = importer.import_strava(client=strava_client)
        self.assertEqual(res.merged, 1)

        self.assertEqual(Ride.objects.count(), 1)
        self.assertEqual(Ride.objects.get().ride_date, actual_ride_day)

    @mock.patch("rides.services.importer.build_thumbnail_file", return_value=None)
    def test_strava_creates_standalone_ride_when_no_rwgps_match_by_default(self, _thumb):
        rwgps_client = mock.Mock(spec=RideWithGPSClient)
        rwgps_client.fetch_rides.return_value = [
            self._rwgps_payload(name="Sentiers de Sherbrooke", distance_m=50200)
        ]
        importer.import_ridewithgps(client=rwgps_client)

        strava_client = mock.Mock(spec=StravaClient)
        strava_client.fetch_rides.return_value = [
            # Different name -> no match -> created standalone (default).
            self._strava_payload(name="Boucle du Lac Memphrémagog", distance_m=50000)
        ]
        res = importer.import_strava(client=strava_client)

        self.assertEqual(res.created, 1)
        self.assertEqual(Ride.objects.count(), 2)

    @mock.patch("rides.services.importer.build_thumbnail_file", return_value=None)
    def test_same_name_different_distance_does_not_merge(self, _thumb):
        rwgps_client = mock.Mock(spec=RideWithGPSClient)
        rwgps_client.fetch_rides.return_value = [
            self._rwgps_payload(name="Sortie du club", distance_m=30000)
        ]
        importer.import_ridewithgps(client=rwgps_client)

        strava_client = mock.Mock(spec=StravaClient)
        strava_client.fetch_rides.return_value = [
            # Same name but a very different distance -> not a match.
            self._strava_payload(name="Sortie du club", distance_m=90000)
        ]
        res = importer.import_strava(client=strava_client)

        self.assertEqual(res.created, 1)
        self.assertEqual(Ride.objects.count(), 2)

    @mock.patch("rides.services.importer.build_thumbnail_file", return_value=None)
    def test_require_strava_match_skips_unmatched_rwgps_routes(self, _thumb):
        rwgps_client = mock.Mock(spec=RideWithGPSClient)
        rwgps_client.fetch_rides.return_value = [
            self._rwgps_payload(name="Solo route, no Strava match", distance_m=20000)
        ]
        res = importer.import_ridewithgps(client=rwgps_client, create_if_unmatched=False)

        self.assertEqual(res.skipped, 1)
        self.assertEqual(Ride.objects.count(), 0)

    @mock.patch("rides.services.importer.build_thumbnail_file", return_value=None)
    def test_require_rwgps_match_skips_unmatched_strava_routes(self, _thumb):
        strava_client = mock.Mock(spec=StravaClient)
        strava_client.fetch_rides.return_value = [
            self._strava_payload(name="Solo route, no RWGPS match", distance_m=20000)
        ]
        res = importer.import_strava(client=strava_client, create_if_unmatched=False)

        self.assertEqual(res.skipped, 1)
        self.assertEqual(Ride.objects.count(), 0)


class ImportCommandTests(TestCase):
    def test_import_runs_ridewithgps_before_strava(self):
        from django.core.management import call_command

        calls = []
        strava_result = importer.ImportResult(created=1)
        rwgps_result = importer.ImportResult(created=1)

        def fake_strava(**kwargs):
            calls.append("strava")
            return strava_result

        def fake_rwgps(**kwargs):
            calls.append("ridewithgps")
            return rwgps_result

        with mock.patch("rides.management.commands.import.import_strava", side_effect=fake_strava), \
             mock.patch("rides.management.commands.import.import_ridewithgps", side_effect=fake_rwgps):
            call_command("import")

        self.assertEqual(calls, ["ridewithgps", "strava"])

    def test_import_defaults_incremental_and_full_flag_overrides(self):
        from django.core.management import call_command

        calls = []

        def fake_strava(**kwargs):
            calls.append(("strava", kwargs))
            return importer.ImportResult()

        def fake_rwgps(**kwargs):
            calls.append(("ridewithgps", kwargs))
            return importer.ImportResult()

        with mock.patch("rides.management.commands.import.import_strava", side_effect=fake_strava), \
             mock.patch("rides.management.commands.import.import_ridewithgps", side_effect=fake_rwgps):
            call_command("import")
            call_command("import", "--full")

        self.assertEqual(
            [(source, kwargs["full"]) for source, kwargs in calls],
            [
                ("ridewithgps", False),
                ("strava", False),
                ("ridewithgps", True),
                ("strava", True),
            ],
        )


@override_settings(RAVITO_POINTS="", PARKING_POINTS="")
class BuildSiteTests(TestCase):
    @override_settings(SITE_BASE_PATH="/Test", SITE_CUSTOM_DOMAIN="www.example.com")
    def test_build_site_writes_pages(self):
        from django.core.management import call_command
        import tempfile

        Ride.objects.create(name="Sortie A", geometry=SQUARE, distance_m=1000, start_city="Magog")
        with tempfile.TemporaryDirectory() as tmp:
            call_command("build_site", output=tmp)
            index = Path(tmp) / "index.html"
            detail = Path(tmp) / "rides" / "sortie-a" / "index.html"
            cname = Path(tmp) / "CNAME"
            self.assertTrue(index.exists())
            self.assertTrue(detail.exists())
            self.assertEqual("www.example.com\n", cname.read_text(encoding="utf-8"))
            html = index.read_text(encoding="utf-8")
            self.assertIn("/Test/assets/css/style.css?v=", html)
            self.assertIn("/Test/assets/js/search.js?v=", html)
            self.assertIn("Sortie A", html)
            self.assertIn("has-ride-cover", html)
            self.assertIn("/Test/assets/img/default-ride-cover.jpg", html)
            self.assertIn('id="sort-by"', html)
            self.assertIn('id="sort-direction"', html)
            self.assertIn('data-direction="asc"', html)
            self.assertIn('<option value="distance">Distance</option>', html)
            self.assertIn('class="sort-direction-icon"', html)
            self.assertIn('id="distance-min"', html)
            self.assertIn('id="distance-max"', html)
            self.assertIn('id="distance-slider"', html)
            self.assertIn('id="elevation-min"', html)
            self.assertIn('id="elevation-max"', html)
            self.assertIn('id="elevation-slider"', html)
            self.assertIn('id="admin-without-ravito-filter"', html)
            self.assertIn('id="admin-without-ravito"', html)
            self.assertIn('id="admin-without-parking-filter"', html)
            self.assertIn('id="admin-without-parking"', html)
            self.assertIn('data-ravitos="0"', html)
            self.assertIn('data-parkings="0"', html)

    @override_settings(SITE_BASE_PATH="", SITE_CUSTOM_DOMAIN="")
    def test_build_site_supports_custom_domain_root_paths(self):
        from django.core.management import call_command
        import tempfile

        Ride.objects.create(name="Sortie A", geometry=SQUARE, distance_m=1000, start_city="Magog")
        with tempfile.TemporaryDirectory() as tmp:
            call_command("build_site", output=tmp)
            index_html = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            detail_html = (
                Path(tmp) / "rides" / "sortie-a" / "index.html"
            ).read_text(encoding="utf-8")

        self.assertIn('href="/assets/css/style.css?v=', index_html)
        self.assertIn('href="/rides/sortie-a/"', index_html)
        self.assertIn('href="/assets/gpx/sortie-a.gpx"', detail_html)
        self.assertIn('<nav class="breadcrumb"><a href="/">', detail_html)
        self.assertNotIn("/GravelleEstrie/", index_html)
        self.assertNotIn("//assets/", index_html)

    @override_settings(SITE_BASE_PATH="/Test")
    def test_build_site_skips_rides_that_start_outside_quebec(self):
        from django.core.management import call_command
        import tempfile

        Ride.objects.create(name="Sortie Québec", geometry=SQUARE, distance_m=1000)
        Ride.objects.create(
            name="Sortie Virginie",
            geometry=[[39.1384, -77.7171], [39.2, -77.7]],
            distance_m=1000,
        )
        with tempfile.TemporaryDirectory() as tmp:
            call_command("build_site", output=tmp)
            index = Path(tmp) / "index.html"
            outside = Path(tmp) / "rides" / "sortie-virginie" / "index.html"
            html = index.read_text(encoding="utf-8")
            self.assertIn("Sortie Québec", html)
            self.assertNotIn("Sortie Virginie", html)
            self.assertFalse(outside.exists())

    @override_settings(SITE_BASE_PATH="/Test")
    def test_build_site_uses_ridewithgps_embed_on_detail_pages(self):
        from django.core.management import call_command
        import tempfile

        Ride.objects.create(
            name="Sortie A",
            geometry=SQUARE,
            distance_m=1000,
            start_city="Magog",
            rwgps_route_id="123",
            ridewithgps_url="https://ridewithgps.com/routes/123",
        )
        with tempfile.TemporaryDirectory() as tmp:
            call_command("build_site", output=tmp)
            detail = Path(tmp) / "rides" / "sortie-a" / "index.html"
            html = detail.read_text(encoding="utf-8")
            self.assertIn(
                'src="https://ridewithgps.com/embeds?type=route&amp;id=123&amp;sampleGraph=true"',
                html,
            )
            self.assertIn('title="Carte RideWithGPS de Sortie A"', html)
            self.assertIn('class="rwgps-embed"', html)
            self.assertIn('width="100%"', html)
            self.assertIn('height="620"', html)
            self.assertIn("/Test/assets/img/default-ride-cover.jpg", html)

    @override_settings(SITE_BASE_PATH="/Test")
    def test_build_site_uses_strava_route_embed_when_no_ridewithgps_embed(self):
        from django.core.management import call_command
        import tempfile

        Ride.objects.create(
            name="Sortie Strava",
            geometry=SQUARE,
            distance_m=65_700,
            elevation_gain_m=804,
            strava_activity_id="3279612223036285112",
            strava_url="https://www.strava.com/routes/3279612223036285112",
        )
        with tempfile.TemporaryDirectory() as tmp:
            call_command("build_site", output=tmp)
            detail = Path(tmp) / "rides" / "sortie-strava" / "index.html"
            html = detail.read_text(encoding="utf-8")

        self.assertIn('class="strava-embed-placeholder"', html)
        self.assertIn('data-embed-type="route"', html)
        self.assertIn('data-embed-id="3279612223036285112"', html)
        self.assertIn('data-full-width="true"', html)
        self.assertIn('data-distance="65700"', html)
        self.assertIn('data-elevation-gain="804"', html)
        self.assertIn('src="https://strava-embeds.com/embed.js"', html)
        self.assertIn("Carte interactive intégrée depuis Strava", html)
        self.assertNotIn("Carte et profil intégrés depuis RideWithGPS", html)

    @override_settings(SITE_BASE_PATH="/Test")
    def test_build_site_writes_gpx_download_for_rides_with_geometry(self):
        from django.core.management import call_command
        import tempfile

        Ride.objects.create(
            name="Sortie A",
            geometry=SQUARE,
            distance_m=1000,
            start_city="Magog",
            description="Belle ride.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            call_command("build_site", output=tmp)
            gpx = Path(tmp) / "assets" / "gpx" / "sortie-a.gpx"
            detail = Path(tmp) / "rides" / "sortie-a" / "index.html"
            html = detail.read_text(encoding="utf-8")
            gpx_exists = gpx.exists()
            root = ET.parse(gpx).getroot()
            ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
            points = root.findall(".//gpx:trkpt", ns)

        self.assertTrue(gpx_exists)
        self.assertEqual("Sortie A", root.findtext(".//gpx:trk/gpx:name", namespaces=ns))
        self.assertEqual(len(SQUARE), len(points))
        self.assertEqual("45", points[0].attrib["lat"])
        self.assertEqual("-72", points[0].attrib["lon"])
        self.assertIn('/Test/assets/gpx/sortie-a.gpx', html)
        self.assertIn("Télécharger GPX", html)
        self.assertIn("download", html)

    @override_settings(SITE_BASE_PATH="/Test")
    def test_build_site_copies_local_ride_images_to_detail_pages(self):
        from django.core.management import call_command
        import tempfile

        ride = Ride.objects.create(
            name="Sortie A",
            geometry=SQUARE,
            distance_m=1000,
            start_city="Magog",
            rwgps_route_id="123",
        )
        with tempfile.TemporaryDirectory() as tmp:
            images_root = Path(tmp) / "images"
            source_dir = images_root / ride.rwgps_route_id
            source_dir.mkdir(parents=True)
            (source_dir / "photo.jpg").write_bytes(b"fake image")

            out = Path(tmp) / "site"
            with self.settings(LOCAL_RIDE_IMAGES_DIR=images_root):
                call_command("build_site", output=out)

            copied = out / "assets" / "ride-images" / ride.slug / "image-1.jpg"
            detail = out / "rides" / ride.slug / "index.html"
            html = detail.read_text(encoding="utf-8")
            copied_exists = copied.exists()
            copied_bytes = copied.read_bytes()

        self.assertTrue(copied_exists)
        self.assertEqual(copied_bytes, b"fake image")
        self.assertIn("/Test/assets/ride-images/sortie-a/image-1.jpg", html)
        self.assertIn('class="ride-photos"', html)
        self.assertIn("--ride-cover-image", html)

    @override_settings(
        SITE_BASE_PATH="/Test",
        RAVITO_POINTS=(
            "https://www.google.com/maps/place/Epicerie+Route/"
            "@45,-71.5,17z/data=!4m6!3m5!8m2!3d45!4d-71.5;"
            "Ravito depart|45|-72;"
            "Ravito arrivee|45|-71"
        ),
        RAVITO_RADIUS_M=500,
        RAVITO_MIN_ROUTE_DISTANCE_M=30_000,
    )
    def test_build_site_shows_nearby_ravitos_on_detail_pages(self):
        from django.core.management import call_command
        import tempfile

        Ride.objects.create(
            name="Sortie A",
            geometry=[[45.0, -72.0], [45.0, -71.0]],
            distance_m=80_000,
        )
        with tempfile.TemporaryDirectory() as tmp:
            call_command("build_site", output=tmp)
            detail = Path(tmp) / "rides" / "sortie-a" / "index.html"
            html = detail.read_text(encoding="utf-8")
            index_html = (Path(tmp) / "index.html").read_text(encoding="utf-8")

        self.assertIn("Ravitos", html)
        self.assertIn("Epicerie Route", html)
        self.assertIn("après ~", html)
        self.assertIn("du parcours", html)
        self.assertIn("https://www.google.com/maps/place/Epicerie+Route/", html)
        self.assertNotIn("Ravito depart", html)
        self.assertNotIn("Ravito arrivee", html)
        self.assertIn('data-ravitos="1"', index_html)

    @override_settings(
        SITE_BASE_PATH="/Test",
        PARKING_POINTS="Parking depart|45|-72;Parking loin|45|-71",
        PARKING_RADIUS_M=500,
    )
    def test_build_site_shows_nearby_parkings_on_detail_pages(self):
        from django.core.management import call_command
        import tempfile

        Ride.objects.create(
            name="Sortie A",
            geometry=[[45.0, -72.0], [45.0, -71.0]],
            distance_m=80_000,
        )
        with tempfile.TemporaryDirectory() as tmp:
            call_command("build_site", output=tmp)
            detail = Path(tmp) / "rides" / "sortie-a" / "index.html"
            html = detail.read_text(encoding="utf-8")

        self.assertIn("Stationnements", html)
        self.assertIn("Parking depart", html)
        self.assertIn("du départ", html)
        self.assertIn("https://www.google.com/maps/search/?api=1&amp;query=45%2C-72", html)
        self.assertNotIn("Parking loin", html)

    @override_settings(SITE_BASE_PATH="/Test")
    def test_build_site_preserves_existing_thumbnails_when_media_file_is_missing(self):
        from django.core.management import call_command
        import tempfile

        Ride.objects.create(
            name="Sortie A",
            geometry=SQUARE,
            distance_m=1000,
            start_city="Magog",
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "site"
            thumbs_dir = out / "assets" / "thumbs"
            thumbs_dir.mkdir(parents=True)
            (thumbs_dir / "sortie-a.png").write_bytes(b"existing thumb")

            call_command("build_site", output=out)

            copied = thumbs_dir / "sortie-a.png"
            html = (out / "index.html").read_text(encoding="utf-8")
            copied_exists = copied.exists()
            copied_bytes = copied.read_bytes()

        self.assertTrue(copied_exists)
        self.assertEqual(copied_bytes, b"existing thumb")
        self.assertIn("/Test/assets/thumbs/sortie-a.png", html)
        self.assertNotIn("Pas de tracé", html)


class DeleteAllAdminViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pw"
        )
        self.client.force_login(self.superuser)

    def test_get_shows_confirmation_without_deleting(self):
        Ride.objects.create(name="A")
        resp = self.client.get(reverse("admin:rides_ride_delete_all"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "supprimer")
        self.assertEqual(Ride.objects.count(), 1)

    def test_post_deletes_all_rides(self):
        Ride.objects.create(name="A")
        Ride.objects.create(name="B")
        resp = self.client.post(reverse("admin:rides_ride_delete_all"))
        self.assertRedirects(resp, reverse("admin:rides_ride_changelist"))
        self.assertEqual(Ride.objects.count(), 0)

    def test_button_present_on_changelist(self):
        resp = self.client.get(reverse("admin:rides_ride_changelist"))
        self.assertContains(resp, reverse("admin:rides_ride_delete_all"))

    def test_local_ride_images_are_visible_and_served_in_admin(self):
        import tempfile

        ride = Ride.objects.create(name="Sortie A", rwgps_route_id="123")
        with tempfile.TemporaryDirectory() as tmp:
            images_root = Path(tmp) / "images"
            source_dir = images_root / ride.rwgps_route_id
            source_dir.mkdir(parents=True)
            (source_dir / "photo.jpg").write_bytes(b"fake image")

            with self.settings(LOCAL_RIDE_IMAGES_DIR=images_root):
                change_url = reverse("admin:rides_ride_change", args=[ride.pk])
                resp = self.client.get(change_url)
                image_url = reverse("admin:rides_ride_local_image", args=[ride.pk, "photo.jpg"])
                image_resp = self.client.get(image_url)

                streamed = b"".join(image_resp.streaming_content)

        self.assertContains(resp, "photo.jpg")
        self.assertContains(resp, image_url)
        self.assertEqual(image_resp.status_code, 200)
        self.assertEqual(streamed, b"fake image")


class StravaAuthCommandTests(TestCase):
    def test_build_authorize_url(self):
        from rides.management.commands.strava_auth import _build_authorize_url

        url = _build_authorize_url("CID", "http://localhost:1234/", "read")
        self.assertTrue(url.startswith("https://www.strava.com/oauth/authorize?"))
        self.assertIn("client_id=CID", url)
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A1234%2F", url)
        self.assertIn("scope=read", url)
        self.assertIn("response_type=code", url)

    def test_exchange_code_for_tokens_posts_expected_payload(self):
        from rides.management.commands.strava_auth import _exchange_code_for_tokens

        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {"refresh_token": "rt123", "athlete": {"id": 42}}
        with mock.patch("rides.management.commands.strava_auth.requests.post", return_value=fake_response) as post:
            tokens = _exchange_code_for_tokens("CID", "SECRET", "CODE")
        self.assertEqual(tokens["refresh_token"], "rt123")
        _, kwargs = post.call_args
        self.assertEqual(
            kwargs["data"],
            {
                "client_id": "CID",
                "client_secret": "SECRET",
                "code": "CODE",
                "grant_type": "authorization_code",
            },
        )

    def test_exchange_code_for_tokens_raises_on_error_status(self):
        from django.core.management.base import CommandError

        from rides.management.commands.strava_auth import _exchange_code_for_tokens

        fake_response = mock.Mock(status_code=400, text="bad code")
        with mock.patch("rides.management.commands.strava_auth.requests.post", return_value=fake_response):
            with self.assertRaises(CommandError):
                _exchange_code_for_tokens("CID", "SECRET", "bad")

    def test_command_writes_refresh_token_to_env_file(self):
        import tempfile

        from django.core.management import call_command

        class FakeServer:
            def __init__(self, address, handler_cls):
                self.server_address = (address[0], 55123)
                self.oauth_code = None
                self.oauth_error = None
                self.timeout = None

            def handle_request(self):
                # Simulate the browser redirect having already happened.
                self.oauth_code = "fake-code"

            def server_close(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("STRAVA_REFRESH_TOKEN=old-token\n")

            fake_tokens = {"refresh_token": "new-refresh-token", "athlete": {"id": 999}}
            with mock.patch(
                "rides.management.commands.strava_auth.HTTPServer", side_effect=FakeServer
            ), mock.patch(
                "rides.management.commands.strava_auth.webbrowser.open"
            ), mock.patch(
                "rides.management.commands.strava_auth._exchange_code_for_tokens",
                return_value=fake_tokens,
            ), self.settings(
                STRAVA_CLIENT_ID="cid", STRAVA_CLIENT_SECRET="secret", STRAVA_ROUTE_IDS=[]
            ):
                out = StringIO()
                call_command("strava_auth", env_file=str(env_path), stdout=out)

            content = env_path.read_text()
        self.assertIn("STRAVA_REFRESH_TOKEN='new-refresh-token'", content)
        self.assertIn("STRAVA_ROUTE_IDS", out.getvalue())

    def test_command_errors_without_client_credentials(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.settings(STRAVA_CLIENT_ID="", STRAVA_CLIENT_SECRET=""):
            with self.assertRaises(CommandError):
                call_command("strava_auth")

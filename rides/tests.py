from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from rides.models import Ride
from rides.services import importer
from rides.services.geometry import bounds, decode_polyline, downsample
from rides.services.ridewithgps import RideWithGPSClient, RWGPSRide
from rides.services.strava import StravaClient, StravaRide


SQUARE = [[45.0, -72.0], [45.1, -72.0], [45.1, -71.9], [45.0, -71.9], [45.0, -72.0]]


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

    def test_fetch_rides_keeps_only_public_ride_type_routes(self):
        client = self._client(route_ids=["1", "2", "3"])
        details = {
            "1": {"id": 1, "type": 1, "private": False, "name": "A", "distance": 1000,
                  "elevation_gain": 10, "map": {"polyline": ""}},
            "2": {"id": 2, "type": 2, "private": False, "name": "B", "distance": 1000,
                  "elevation_gain": 10, "map": {"polyline": ""}},  # run -> excluded
            "3": {"id": 3, "type": 1, "private": True, "name": "C", "distance": 1000,
                  "elevation_gain": 10, "map": {"polyline": ""}},  # private -> excluded
        }
        with mock.patch.object(StravaClient, "_get_route", side_effect=lambda rid: details[rid]):
            rides = client.fetch_rides()
        self.assertEqual({r.external_id for r in rides}, {"1"})

    def test_route_urls_and_no_ride_date(self):
        client = self._client(route_ids=["42"])
        details = {"42": {"id": 42, "type": 1, "private": False, "name": "Boucle",
                           "distance": 5000, "elevation_gain": 100, "map": {"polyline": ""}}}
        with mock.patch.object(StravaClient, "_get_route", side_effect=lambda rid: details[rid]):
            rides = client.fetch_rides()
        self.assertEqual(len(rides), 1)
        self.assertEqual(rides[0].strava_url, "https://www.strava.com/routes/42")
        self.assertIsNone(rides[0].ride_date)

    def test_fetch_rides_returns_empty_without_route_ids_configured(self):
        # STRAVA_ROUTE_IDS is optional (Strava is a secondary source) — an
        # empty list means "nothing to do", not an error.
        client = self._client(route_ids=[])
        self.assertEqual(client.fetch_rides(), [])

    def test_fetch_rides_skips_existing_route_ids_before_api_fetch(self):
        client = self._client(route_ids=["42"])
        with mock.patch.object(StravaClient, "_get_route") as get_route:
            rides = client.fetch_rides(skip_route_ids={"42"})
        self.assertEqual(rides, [])
        get_route.assert_not_called()


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
                            "activity_types": ["cycling:gravel"], "track_points": []}},
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
                            "activity_types": ["cycling:gravel"], "track_points": []}},
        }
        with mock.patch.object(RideWithGPSClient, "iter_route_summaries", return_value=summaries), \
             mock.patch.object(RideWithGPSClient, "_get", side_effect=lambda path, **p: details[int(path.split("/")[-1].split(".")[0])]) as get_detail:
            rides = client.fetch_rides(skip_route_ids={"20"})
        self.assertEqual({r.external_id for r in rides}, {"10"})
        self.assertEqual(get_detail.call_count, 1)

    @override_settings(RWGPS_EXCLUDED_ROUTE_IDS=["20"])
    def test_fetch_rides_skips_excluded_route_ids(self):
        client = RideWithGPSClient(api_key="k", user_id="1")
        summaries = [{"id": 10}, {"id": 20}]
        details = {
            10: {"route": {"id": 10, "name": "Gravel loop", "distance": 1000,
                            "activity_types": ["cycling:gravel"], "track_points": []}},
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


class BuildSiteTests(TestCase):
    @override_settings(SITE_BASE_PATH="/Test")
    def test_build_site_writes_pages(self):
        from django.core.management import call_command
        import tempfile

        Ride.objects.create(name="Sortie A", geometry=SQUARE, distance_m=1000, start_city="Magog")
        with tempfile.TemporaryDirectory() as tmp:
            call_command("build_site", output=tmp)
            index = Path(tmp) / "index.html"
            detail = Path(tmp) / "rides" / "sortie-a" / "index.html"
            self.assertTrue(index.exists())
            self.assertTrue(detail.exists())
            html = index.read_text(encoding="utf-8")
            self.assertIn("/Test/assets/css/style.css", html)
            self.assertIn("Sortie A", html)
            self.assertIn('id="distance-min"', html)
            self.assertIn('id="distance-max"', html)
            self.assertIn('id="elevation-min"', html)
            self.assertIn('id="elevation-max"', html)

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
            self.assertIn('width="100%"', html)
            self.assertIn('height="620"', html)


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

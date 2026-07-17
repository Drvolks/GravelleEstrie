"""Generate the static website published to GitHub Pages.

Renders an index (with client-side search/filter) plus one detail page per
published ride into ``settings.SITE_OUTPUT_DIR``. Fully self-contained: copies
the pre-rendered thumbnail PNGs and static assets, no runtime API or JS map.
"""
from __future__ import annotations

import hashlib
import math
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from django.conf import settings
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string

from rides.models import Ride
from rides.services.images import list_ride_images
from rides.services.location import geometry_starts_in_quebec, infer_start_city
from rides.services.ravitos import (
    find_nearby_parking,
    find_nearby_ravitos,
    parse_parking_points,
    parse_ravito_points,
)

DEFAULT_RIDE_COVER = "default-ride-cover.jpg"
GPX_NS = "http://www.topografix.com/GPX/1/1"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

ET.register_namespace("", GPX_NS)
ET.register_namespace("xsi", XSI_NS)


class Command(BaseCommand):
    help = "Build the static site into SITE_OUTPUT_DIR."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            help="Override the output directory (defaults to SITE_OUTPUT_DIR).",
        )

    def handle(self, *args, **options):
        out = Path(options["output"]) if options.get("output") else settings.SITE_OUTPUT_DIR
        base_path = settings.SITE_BASE_PATH

        with tempfile.TemporaryDirectory() as tmp:
            thumb_backup_dir = self._backup_existing_dir(
                out / "assets" / "thumbs", Path(tmp) / "thumbs"
            )

            # Clear the directory's *contents* rather than the directory itself,
            # so it works when `out` is a mounted volume (e.g. in Docker).
            out.mkdir(parents=True, exist_ok=True)
            for child in out.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()

            self._copy_assets(out)
            thumbs_dir = out / "assets" / "thumbs"
            thumbs_dir.mkdir(parents=True, exist_ok=True)
            asset_version = self._asset_version(out / "assets")

            rides_qs = Ride.objects.published()
            if settings.RWGPS_EXCLUDED_ROUTE_IDS:
                rides_qs = rides_qs.exclude(rwgps_route_id__in=settings.RWGPS_EXCLUDED_ROUTE_IDS)
            rides = [ride for ride in rides_qs if geometry_starts_in_quebec(ride.geometry)]
            ravitos = parse_ravito_points(settings.RAVITO_POINTS)
            parkings = parse_parking_points(settings.PARKING_POINTS)
            views = [
                self._ride_view(
                    r,
                    base_path,
                    out,
                    thumbs_dir,
                    thumb_backup_dir,
                    ravitos,
                    parkings,
                )
                for r in rides
            ]

            max_distance = self._ceil_max((v.distance_km for v in views), default=100, step=10)
            max_elevation = self._ceil_max((v.elevation_m for v in views), default=1000, step=100)

            common = {
                "base_path": base_path,
                "site_title": settings.SITE_TITLE,
                "site_tagline": settings.SITE_TAGLINE,
                "default_cover_url": self._default_cover_url(base_path),
                "asset_version": asset_version,
            }

            # Index
            (out / "index.html").write_text(
                self._render_template(
                    "site/index.html",
                    {
                        **common,
                        "rides": views,
                        "max_distance": max_distance,
                        "max_elevation": max_elevation,
                    },
                ),
                encoding="utf-8",
            )

            # Detail pages at /rides/<slug>/index.html
            for view in views:
                ride_dir = out / "rides" / view.slug
                ride_dir.mkdir(parents=True, exist_ok=True)
                (ride_dir / "index.html").write_text(
                    self._render_template("site/detail.html", {**common, "ride": view}),
                    encoding="utf-8",
                )

            # Tell GitHub Pages not to run Jekyll (keeps files predictable).
            (out / ".nojekyll").write_text("", encoding="utf-8")
            if settings.SITE_CUSTOM_DOMAIN:
                (out / "CNAME").write_text(
                    f"{settings.SITE_CUSTOM_DOMAIN}\n",
                    encoding="utf-8",
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Site généré : {len(views)} sortie(s) → {out}"
            )
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _render_template(template_name: str, context: dict) -> str:
        html = render_to_string(template_name, context)
        return "\n".join(line.rstrip() for line in html.splitlines()) + "\n"

    def _copy_assets(self, out: Path):
        src = Path(settings.BASE_DIR) / "rides" / "static_src"
        dest = out / "assets"
        shutil.copytree(src, dest, dirs_exist_ok=True)

    @staticmethod
    def _asset_version(assets_dir: Path) -> str:
        digest = hashlib.sha256()
        for relative in (
            Path("css/style.css"),
            Path("js/gallery.js"),
            Path("js/search.js"),
        ):
            path = assets_dir / relative
            if path.exists():
                digest.update(relative.as_posix().encode("utf-8"))
                digest.update(path.read_bytes())
        return digest.hexdigest()[:12]

    @staticmethod
    def _backup_existing_dir(source: Path, dest: Path) -> Path | None:
        if not source.is_dir():
            return None
        shutil.copytree(source, dest)
        return dest

    def _ride_view(
        self,
        ride: Ride,
        base_path: str,
        out: Path,
        thumbs_dir: Path,
        thumb_backup_dir: Path | None,
        ravitos: list,
        parkings: list,
    ) -> SimpleNamespace:
        thumb_url = ""
        dest = thumbs_dir / f"{ride.slug}.png"
        if ride.thumbnail and Path(ride.thumbnail.path).exists():
            shutil.copyfile(ride.thumbnail.path, dest)
        elif thumb_backup_dir:
            backup = thumb_backup_dir / dest.name
            if backup.exists():
                shutil.copyfile(backup, dest)
        if dest.exists():
            thumb_url = f"{base_path}/assets/thumbs/{ride.slug}.png"

        images = self._copy_ride_images(ride, base_path, out)
        gpx_url = self._write_gpx_file(ride, base_path, out)
        nearby_ravitos = self._nearby_ravito_views(ride, ravitos)
        nearby_parkings = self._nearby_parking_views(ride, parkings)

        start_city = ride.start_city or infer_start_city(ride.geometry)

        return SimpleNamespace(
            name=ride.name,
            slug=ride.slug,
            description=ride.description,
            ride_date=ride.ride_date,
            created_at=ride.created_at,
            start_city=start_city,
            distance_km=ride.distance_km,
            distance_m=round(ride.distance_m),
            elevation_m=ride.elevation_m,
            strava_url=ride.strava_url,
            strava_embed_id=ride.strava_activity_id,
            ridewithgps_url=ride.ridewithgps_url,
            ridewithgps_embed_url=self._ridewithgps_embed_url(ride),
            thumb_url=thumb_url,
            images=images,
            ravitos=nearby_ravitos,
            ravito_count=len(nearby_ravitos),
            parkings=nearby_parkings,
            parking_count=len(nearby_parkings),
            gpx_url=gpx_url,
            cover_image_url=(
                images[0].url if images else self._default_cover_url(base_path)
            ),
        )

    @staticmethod
    def _default_cover_url(base_path: str) -> str:
        return f"{base_path}/assets/img/{DEFAULT_RIDE_COVER}"

    def _copy_ride_images(self, ride: Ride, base_path: str, out: Path) -> list[SimpleNamespace]:
        images = []
        dest_dir = out / "assets" / "ride-images" / ride.slug
        for index, image in enumerate(list_ride_images(ride), start=1):
            ext = image.path.suffix.lower()
            dest = dest_dir / f"image-{index}{ext}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(image.path, dest)
            images.append(
                SimpleNamespace(
                    url=f"{base_path}/assets/ride-images/{ride.slug}/{dest.name}",
                    filename=image.filename,
                    alt=f"Photo de la sortie {ride.name}",
                )
            )
        return images

    def _write_gpx_file(self, ride: Ride, base_path: str, out: Path) -> str:
        points = self._geometry_points(ride.geometry)
        if len(points) < 2:
            return ""

        dest_dir = out / "assets" / "gpx"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{ride.slug}.gpx"

        root = ET.Element(
            f"{{{GPX_NS}}}gpx",
            {
                "version": "1.1",
                "creator": settings.SITE_TITLE,
                f"{{{XSI_NS}}}schemaLocation": (
                    f"{GPX_NS} http://www.topografix.com/GPX/1/1/gpx.xsd"
                ),
            },
        )
        metadata = ET.SubElement(root, f"{{{GPX_NS}}}metadata")
        ET.SubElement(metadata, f"{{{GPX_NS}}}name").text = ride.name
        if ride.description:
            ET.SubElement(metadata, f"{{{GPX_NS}}}desc").text = ride.description

        trk = ET.SubElement(root, f"{{{GPX_NS}}}trk")
        ET.SubElement(trk, f"{{{GPX_NS}}}name").text = ride.name
        trkseg = ET.SubElement(trk, f"{{{GPX_NS}}}trkseg")
        for lat, lon in points:
            ET.SubElement(
                trkseg,
                f"{{{GPX_NS}}}trkpt",
                {
                    "lat": self._format_coordinate(lat),
                    "lon": self._format_coordinate(lon),
                },
            )

        ET.indent(root, space="  ")
        ET.ElementTree(root).write(dest, encoding="utf-8", xml_declaration=True)
        return f"{base_path}/assets/gpx/{ride.slug}.gpx"

    def _nearby_ravito_views(self, ride: Ride, ravitos: list) -> list[SimpleNamespace]:
        matches = find_nearby_ravitos(
            ride.geometry,
            ravitos,
            radius_m=settings.RAVITO_RADIUS_M,
            min_route_distance_m=settings.RAVITO_MIN_ROUTE_DISTANCE_M,
            endpoint_exclusion_radius_m=settings.RAVITO_ENDPOINT_EXCLUSION_RADIUS_M,
        )
        return [
            SimpleNamespace(
                name=match.ravito.name,
                lat=match.ravito.lat,
                lng=match.ravito.lng,
                distance_m=round(match.distance_m),
                distance_label=self._ravito_distance_label(match.distance_m),
                route_distance_km=round(match.route_distance_m / 1000, 1),
                route_distance_label=self._ravito_route_distance_label(
                    match.route_distance_m
                ),
                map_url=(
                    match.ravito.url
                    or self._ravito_map_url(match.ravito.lat, match.ravito.lng)
                ),
            )
            for match in matches
        ]

    def _nearby_parking_views(self, ride: Ride, parkings: list) -> list[SimpleNamespace]:
        matches = find_nearby_parking(
            ride.geometry,
            parkings,
            radius_m=settings.PARKING_RADIUS_M,
        )
        return [
            SimpleNamespace(
                name=match.parking.name,
                lat=match.parking.lat,
                lng=match.parking.lng,
                distance_m=round(match.distance_m),
                distance_label=self._point_distance_label(match.distance_m, "du départ"),
                map_url=(
                    match.parking.url
                    or self._map_url(match.parking.lat, match.parking.lng)
                ),
            )
            for match in matches
        ]

    @classmethod
    def _ravito_map_url(cls, lat: float, lng: float) -> str:
        return cls._map_url(lat, lng)

    @classmethod
    def _map_url(cls, lat: float, lng: float) -> str:
        query = urlencode(
            {
                "api": "1",
                "query": f"{cls._format_coordinate(lat)},{cls._format_coordinate(lng)}",
            }
        )
        return f"https://www.google.com/maps/search/?{query}"

    @staticmethod
    def _ravito_distance_label(distance_m: float) -> str:
        return Command._point_distance_label(distance_m, "du parcours")

    @staticmethod
    def _point_distance_label(distance_m: float, suffix: str) -> str:
        if distance_m >= 1000:
            distance_km = f"{distance_m / 1000:.1f}".replace(".", ",")
            return f"~{distance_km} km {suffix}"
        return f"~{int(round(distance_m))} m {suffix}"

    @staticmethod
    def _ravito_route_distance_label(route_distance_m: float) -> str:
        distance_km = f"{route_distance_m / 1000:.1f}".replace(".", ",")
        return f"après ~{distance_km} km"

    @staticmethod
    def _geometry_points(geometry) -> list[tuple[float, float]]:
        points = []
        for point in geometry or []:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                lat = float(point[0])
                lon = float(point[1])
            except (TypeError, ValueError):
                continue
            points.append((lat, lon))
        return points

    @staticmethod
    def _format_coordinate(value: float) -> str:
        return f"{value:.7f}".rstrip("0").rstrip(".")

    @staticmethod
    def _ridewithgps_embed_url(ride: Ride) -> str:
        if not ride.rwgps_route_id:
            return ""
        query = urlencode(
            {
                "type": "route",
                "id": ride.rwgps_route_id,
                "sampleGraph": "true",
            }
        )
        return f"https://ridewithgps.com/embeds?{query}"

    @staticmethod
    def _ceil_max(values, *, default: int, step: int) -> int:
        vals = [v for v in values if v]
        if not vals:
            return default
        top = max(vals)
        return int(math.ceil(top / step) * step)

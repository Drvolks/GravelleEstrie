from django.db import models
from django.utils.text import slugify


class RideQuerySet(models.QuerySet):
    def published(self):
        return self.filter(is_published=True)


class Ride(models.Model):
    """A single club ride/route consolidated from Strava or RideWithGPS.

    Route geometry is stored as a list of ``[lat, lng]`` pairs in ``geometry``
    (JSON). Thumbnails are baked from that geometry at import time.
    """

    class Source(models.TextChoices):
        MANUAL = "manual", "Saisie manuelle"
        STRAVA = "strava", "Strava"
        RWGPS = "ridewithgps", "RideWithGPS"

    # Identity
    name = models.CharField("Nom", max_length=200)
    slug = models.SlugField("Slug", max_length=220, unique=True, blank=True)
    description = models.TextField("Description", blank=True)
    ride_date = models.DateField("Date de la sortie", null=True, blank=True)

    # Location
    start_city = models.CharField("Ville de départ", max_length=120, blank=True)

    # Stats
    distance_m = models.FloatField("Distance (m)", default=0)
    elevation_gain_m = models.FloatField("Dénivelé positif (m)", default=0)

    # Geometry: list of [lat, lng] pairs (WGS84).
    geometry = models.JSONField("Tracé (points lat/lng)", default=list, blank=True)

    # Provenance / links.
    #
    # `source` records which source first created this row (informational —
    # shown in the admin/list filter). Cross-source matching does not use it:
    # `strava_activity_id` and `rwgps_route_id` are independent nullable
    # fields, so a ride imported from Strava and later matched to a
    # RideWithGPS route ends up with *both* set on the same row instead of
    # two separate rides. `external_id` is only used for non-API (manual)
    # entries that need their own dedup key, e.g. the demo seed data.
    source = models.CharField(
        "Source", max_length=20, choices=Source.choices, default=Source.MANUAL
    )
    external_id = models.CharField("Identifiant externe (saisie manuelle)", max_length=64, blank=True)
    strava_activity_id = models.CharField("ID activité Strava", max_length=64, blank=True)
    rwgps_route_id = models.CharField("ID parcours RideWithGPS", max_length=64, blank=True)
    strava_url = models.URLField("Lien Strava", blank=True)
    ridewithgps_url = models.URLField("Lien RideWithGPS", blank=True)

    # Rendered assets
    thumbnail = models.ImageField(
        "Vignette", upload_to="thumbnails/", blank=True, null=True
    )

    is_published = models.BooleanField("Publiée", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = RideQuerySet.as_manager()

    class Meta:
        ordering = ["-ride_date", "name"]
        verbose_name = "Sortie"
        verbose_name_plural = "Sorties"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"],
                condition=~models.Q(external_id=""),
                name="unique_source_external_id",
            ),
            models.UniqueConstraint(
                fields=["strava_activity_id"],
                condition=~models.Q(strava_activity_id=""),
                name="unique_strava_activity_id",
            ),
            models.UniqueConstraint(
                fields=["rwgps_route_id"],
                condition=~models.Q(rwgps_route_id=""),
                name="unique_rwgps_route_id",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(self.name) or "sortie"
        slug = base
        i = 2
        qs = Ride.objects.all()
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        while qs.filter(slug=slug).exists():
            slug = f"{base}-{i}"
            i += 1
        return slug

    # --- Convenience accessors used by templates / the site builder --------

    @property
    def distance_km(self) -> float:
        return round(self.distance_m / 1000.0, 1)

    @property
    def elevation_m(self) -> int:
        return int(round(self.elevation_gain_m))

    @property
    def has_geometry(self) -> bool:
        return bool(self.geometry) and len(self.geometry) >= 2

    @property
    def is_cross_linked(self) -> bool:
        """True once this ride has been matched across both Strava and RWGPS."""
        return bool(self.strava_activity_id) and bool(self.rwgps_route_id)

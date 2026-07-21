from django.contrib import admin, messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from .models import Ride
from .services.images import expected_ride_image_dir, list_ride_images
from .services.thumbnails import build_thumbnail_file


@admin.register(Ride)
class RideAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "ride_date",
        "start_city",
        "distance_km_display",
        "elevation_display",
        "source",
        "linked_sources",
        "local_image_count",
        "is_published",
        "thumb_preview",
    )
    list_filter = ("source", "is_published", "start_city")
    search_fields = ("name", "start_city", "description")
    list_editable = ("is_published",)
    date_hierarchy = "ride_date"
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = (
        "created_at",
        "updated_at",
        "thumb_preview",
        "point_count",
        "local_image_folder",
        "local_images_preview",
    )
    actions = ("regenerate_thumbnails",)

    fieldsets = (
        (None, {"fields": ("name", "slug", "description", "ride_date", "is_published")}),
        (
            "Lieu & statistiques",
            {
                "fields": (
                    "start_city",
                    "distance_m",
                    "elevation_gain_m",
                    "strava_elevation_gain_m",
                )
            },
        ),
        ("Tracé", {"fields": ("geometry", "point_count", "thumbnail", "thumb_preview")}),
        ("Images locales", {"fields": ("local_image_folder", "local_images_preview")}),
        (
            "Source & liens",
            {
                "fields": (
                    "source",
                    "external_id",
                    "strava_activity_id",
                    "strava_url",
                    "rwgps_route_id",
                    "ridewithgps_url",
                ),
                "description": (
                    "Une sortie importée à la fois de Strava et de RideWithGPS est "
                    "automatiquement fusionnée sur une seule fiche (même nom, distance "
                    "similaire) plutôt que dupliquée. RideWithGPS est fusionné dans une "
                    "sortie Strava existante uniquement — un parcours sans correspondance "
                    "est ignoré."
                ),
            },
        ),
        ("Métadonnées", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Distance", ordering="distance_m")
    def distance_km_display(self, obj):
        return f"{obj.distance_km} km"

    @admin.display(description="Dénivelé", ordering="elevation_gain_m")
    def elevation_display(self, obj):
        if obj.strava_elevation_gain_m:
            return f"{obj.elevation_m} m (Strava)"
        return f"{obj.elevation_m} m"

    @admin.display(description="Points")
    def point_count(self, obj):
        return len(obj.geometry or [])

    @admin.display(description="Liens")
    def linked_sources(self, obj):
        links = []
        if obj.strava_url:
            links.append(format_html('<a href="{}" target="_blank">Strava</a>', obj.strava_url))
        if obj.ridewithgps_url:
            links.append(
                format_html('<a href="{}" target="_blank">RWGPS</a>', obj.ridewithgps_url)
            )
        return mark_safe(" + ".join(links)) if links else "—"

    @admin.display(description="Vignette")
    def thumb_preview(self, obj):
        if obj.thumbnail:
            return format_html(
                '<img src="{}" style="height:60px;border-radius:4px" />', obj.thumbnail.url
            )
        return "—"

    @admin.display(description="Images")
    def local_image_count(self, obj):
        count = len(list_ride_images(obj))
        return count if count else "—"

    @admin.display(description="Dossier")
    def local_image_folder(self, obj):
        if obj is None:
            return "—"
        folder = expected_ride_image_dir(obj)
        try:
            display_path = folder.relative_to(folder.parents[1])
        except (IndexError, ValueError):
            display_path = folder
        return format_html("<code>{}</code>", display_path)

    @admin.display(description="Aperçu")
    def local_images_preview(self, obj):
        if obj is None:
            return "—"
        images = list_ride_images(obj)
        if not images:
            return format_html(
                '<span style="color:#6b7280">Aucune image trouvée dans <code>{}</code>.</span>',
                expected_ride_image_dir(obj),
            )

        rows = []
        for image in images:
            url = reverse("admin:rides_ride_local_image", args=[obj.pk, image.filename])
            rows.append((url, image.filename, image.filename))
        thumbs = format_html_join(
            "",
            (
                '<a href="{}" target="_blank" rel="noopener" '
                'style="display:block;width:150px;aspect-ratio:4/3;overflow:hidden;'
                'border-radius:10px;border:1px solid rgba(20,38,77,.14);'
                'background:#f6f3ec">'
                '<img src="{}" alt="{}" loading="lazy" '
                'style="width:100%;height:100%;object-fit:cover;display:block">'
                "</a>"
            ),
            rows,
        )
        return format_html(
            '<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start">{}</div>',
            thumbs,
        )

    @admin.action(description="Régénérer les vignettes du tracé")
    def regenerate_thumbnails(self, request, queryset):
        done, failed = 0, 0
        for ride in queryset:
            try:
                thumb = build_thumbnail_file(ride)
                if thumb is None:
                    continue
                ride.thumbnail.save(thumb.name, thumb, save=True)
                done += 1
            except Exception as exc:  # noqa: BLE001 - surface to admin as a message
                failed += 1
                self.message_user(request, f"{ride.name}: {exc}", level=messages.ERROR)
        self.message_user(
            request, f"{done} vignette(s) régénérée(s), {failed} échec(s).",
            level=messages.SUCCESS if not failed else messages.WARNING,
        )

    # --- "Delete all" — a standalone button on the changelist, independent of
    # row selection, with its own confirmation page since it's irreversible. --

    def get_urls(self):
        custom = [
            path(
                "delete-all/",
                self.admin_site.admin_view(self.delete_all_view),
                name="rides_ride_delete_all",
            ),
            path(
                "<int:object_id>/local-images/<path:filename>/",
                self.admin_site.admin_view(self.local_image_view),
                name="rides_ride_local_image",
            ),
        ]
        return custom + super().get_urls()

    def local_image_view(self, request, object_id, filename):
        ride = self.get_object(request, object_id)
        if ride is None:
            raise Http404("Ride not found")
        for image in list_ride_images(ride):
            if image.filename == filename:
                return FileResponse(open(image.path, "rb"))
        raise Http404("Image not found")

    def delete_all_view(self, request):
        count = Ride.objects.count()
        if request.method == "POST":
            Ride.objects.all().delete()
            self.message_user(
                request, f"{count} sortie(s) supprimée(s).", level=messages.WARNING
            )
            return redirect("admin:rides_ride_changelist")
        context = {
            **self.admin_site.each_context(request),
            "title": "Supprimer toutes les sorties ?",
            "count": count,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/rides/ride/delete_all_confirm.html", context)

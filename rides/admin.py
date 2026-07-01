from django.contrib import admin, messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import Ride
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
        "is_published",
        "thumb_preview",
    )
    list_filter = ("source", "is_published", "start_city")
    search_fields = ("name", "start_city", "description")
    list_editable = ("is_published",)
    date_hierarchy = "ride_date"
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at", "thumb_preview", "point_count")
    actions = ("regenerate_thumbnails",)

    fieldsets = (
        (None, {"fields": ("name", "slug", "description", "ride_date", "is_published")}),
        ("Lieu & statistiques", {"fields": ("start_city", "distance_m", "elevation_gain_m")}),
        ("Tracé", {"fields": ("geometry", "point_count", "thumbnail", "thumb_preview")}),
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
        ]
        return custom + super().get_urls()

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

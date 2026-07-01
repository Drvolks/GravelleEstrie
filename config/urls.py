from django.conf import settings
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import path, re_path
from django.views.static import serve

admin.site.site_header = "Gravelle Estrie — administration"
admin.site.site_title = "Gravelle Estrie admin"
admin.site.index_title = "Gestion des sorties"

urlpatterns = [
    path("", lambda request: redirect("admin:index")),
    path("admin/", admin.site.urls),
    # Serve uploaded thumbnails so admin previews work under gunicorn too.
    # This is a back-office tool; media is not part of the published site.
    re_path(
        r"^media/(?P<path>.*)$",
        serve,
        {"document_root": settings.MEDIA_ROOT},
    ),
]

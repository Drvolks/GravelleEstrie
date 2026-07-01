"""Django settings for the Gravelle Estrie ride tracker.

The Django app is the admin/back-office side: it stores rides in Postgres
(SQLite for local dev) and renders a static site published to GitHub Pages.
"""
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env if present (no-op in production where env vars are set directly).
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rides",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves collected static files (admin CSS/JS) directly from gunicorn.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database: sqlite locally when DATABASE_URL is empty, Postgres in production.
DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-ca"
TIME_ZONE = "America/Toronto"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# WhiteNoise: compress collected static files (no manifest, so it also works
# with DEBUG=True and without a prior collectstatic run).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Logging -----------------------------------------------------------------
LOG_LEVEL = os.environ.get("DJANGO_LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "rides": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}

# --- Project-specific settings ---------------------------------------------

# Where `build_site` writes the generated static website.
SITE_OUTPUT_DIR = BASE_DIR / "docs"

# Sub-path the committed static site is served from (e.g. "/GravelleEstrie").
SITE_BASE_PATH = os.environ.get("SITE_BASE_PATH", "/GravelleEstrie").rstrip("/")

SITE_TITLE = "Gravelle Estrie"
SITE_TAGLINE = "Sorties gravelle du club Gravelle Estrie"

# Strava API
STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
STRAVA_REFRESH_TOKEN = os.environ.get("STRAVA_REFRESH_TOKEN", "")
# Informational only — Strava's API can't list another athlete's routes (see
# strava.py), so this isn't used to fetch anything. Kept for reference.
STRAVA_ATHLETE_ID = os.environ.get("STRAVA_ATHLETE_ID", "89793076")
# Comma-separated numeric route ids to import (Strava has no "list this
# athlete's public routes" API for anyone but the authenticated athlete
# themselves — see strava.py for why). Collect these manually from the
# athlete's public profile.
STRAVA_ROUTE_IDS = [
    rid.strip() for rid in os.environ.get("STRAVA_ROUTE_IDS", "").split(",") if rid.strip()
]

# RideWithGPS API
RWGPS_API_KEY = os.environ.get("RWGPS_API_KEY", "")
RWGPS_AUTH_TOKEN = os.environ.get("RWGPS_AUTH_TOKEN", "")
RWGPS_USER_ID = os.environ.get("RWGPS_USER_ID", "4058724")
# Comma-separated RideWithGPS route ids to ignore even if RideWithGPS marks
# them as cycling. These known running routes are currently tagged
# ``cycling:gravel`` at the source.
RWGPS_EXCLUDED_ROUTE_IDS = [
    rid.strip()
    for rid in os.environ.get("RWGPS_EXCLUDED_ROUTE_IDS", "45012570,45178724").split(",")
    if rid.strip()
]

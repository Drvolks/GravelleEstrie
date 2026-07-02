# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Django back-office (Postgres/SQLite + admin console) that imports cycling club rides
from Strava and RideWithGPS, bakes map thumbnails, and generates a **static site**
committed to `docs/` for GitHub Pages. Django itself is never deployed — production
is just the static HTML/CSS/JS in `docs/`.

```
Strava / RideWithGPS ──import──▶  Django + Postgres  ──build_site──▶  docs/ (static, committed)
                                  (admin console)        thumbnails baked from route geometry
```

## Commands

Everything normally runs through Docker Compose; a local venv works too (falls back to
SQLite when `DATABASE_URL` is unset).

```bash
docker compose up --build                                       # admin at :8000/admin/
docker compose run --rm web python manage.py import              # import_ridewithgps then import_strava
docker compose run --rm web python manage.py build_site           # -> ./docs
docker compose run --rm web python manage.py seed_demo            # demo data, no credentials needed
docker compose run --rm web python manage.py test rides           # tests
docker compose run --rm web python manage.py test rides.tests.SomeTestCase.test_thing  # single test

docker compose --profile preview up -d preview   # serve docs/ at :8080/ like the custom domain
```

**The `web` image bakes in a `COPY . .` snapshot at build time — it is not a live mount
of the working tree.** After editing any Python/template/CSS file, run
`docker compose build web` (or `docker compose run --rm --build web ...`) before
`build_site`, or the container will silently regenerate `docs/` from stale code.

⚠️ **Always run `import` and `build_site` against the same database.** Each reads
whatever `DATABASE_URL` it's configured for. Mixing a Dockerized `import` (Postgres)
with a local `build_site` (SQLite fallback) silently produces a site from the wrong
(likely demo) data. Keep both steps in Docker, or point a local `.env` at the same
Postgres via `postgres://gravelle:gravelle@localhost:5432/gravelle_estrie` (the `db`
service exposes 5432 to the host).

For a throwaway preview outside Docker:
```bash
.venv/bin/python manage.py build_site --output preview
python -m http.server 8765 --directory preview
```

## Architecture

- `config/` — Django project (settings, urls, wsgi). Key settings: `SITE_OUTPUT_DIR`
  (defaults to `docs/`), `SITE_BASE_PATH` (defaults to empty for the
  `www.gravelleestrie.com` custom-domain root; set `/GravelleEstrie` only for project
  pages), and `SITE_CUSTOM_DOMAIN` (defaults to `www.gravelleestrie.com` and is written
  to `docs/CNAME` by `build_site`).
- `rides/models.py` — single `Ride` model. `source` records provenance only;
  cross-source linking is via independent nullable `strava_activity_id` /
  `rwgps_route_id` fields on the *same row* (a ride can have both). `geometry` is a
  JSON list of `[lat, lng]` pairs used to bake thumbnails.
- `rides/services/` — `strava.py`, `ridewithgps.py` (API clients), `geometry.py`,
  `location.py` (Quebec start-point filtering), `thumbnails.py` (renders PNGs from
  route geometry + OSM tiles), `images.py` (discovers git-ignored local ride photos),
  `importer.py`.
- `rides/management/commands/`:
  - `import.py` runs `import_ridewithgps` then `import_strava` in order.
  - `import_ridewithgps.py` is the **bulk, primary** source — lists all of a user's
    routes via pagination, then also fetches any direct ids in
    `RWGPS_EXTRA_ROUTE_IDS`.
  - `import_strava.py` is **secondary/optional**: Strava's public API cannot list
    another athlete's routes at all (`GET /athletes/{id}/routes` 403s for anyone but
    the authenticated athlete — no way around it for third-party apps), so it only
    ever fetches the explicit route ids in `STRAVA_ROUTE_IDS` one at a time via
    `GET /routes/{id}` (the mechanism behind shareable route links, works regardless
    of who authorized the token). It matches onto an existing RideWithGPS-sourced
    ride by same name (case/whitespace-insensitive) + distance within 15%, merging
    links onto one row, or creates a standalone ride if nothing matches.
  - Both importers are incremental by default (skip already-imported ids); pass
    `--full` to refetch everything, `--require-strava-match` /
    `--require-rwgps-match` for strict enrich-only runs, `--no-thumbnails` to skip
    tile downloads.
  - Both importers only keep **cycling** routes starting in **Quebec**: RideWithGPS
    filters on `activity_types` (`cycling:*`; untagged routes assumed cycling; known
    bad tags suppressed via `RWGPS_EXCLUDED_ROUTE_IDS`; direct ids can be added with
    `RWGPS_EXTRA_ROUTE_IDS`), Strava on route `type` `1` and `6` (gravel rides come
    back as `6`) plus decoded-polyline start point.
  - `build_site.py` — reads published rides, renders `site/index.html` +
    `site/detail.html` per ride into `SITE_OUTPUT_DIR`, copies `rides/static_src/`
    to `assets/`, copies thumbnail PNGs to `assets/thumbs/`, and copies local photos
    from `images/<ride id>/` to `assets/ride-images/<slug>/`. Local photo lookup tries
    `rwgps_route_id` first, then Strava/manual ids, pk, and slug. Detail pages use the
    first local photo as a subtle background, falling back to
    `assets/img/default-ride-cover.jpg`. It also writes Garmin-compatible GPX Track
    files from stored `Ride.geometry` to `assets/gpx/<slug>.gpx` and links them from
    the detail page. No runtime API or JS map in the output — thumbnails and the
    RideWithGPS iframe embed are the only map rendering.
  - `strava_auth.py` — one-time local-server OAuth flow that writes
    `STRAVA_REFRESH_TOKEN` into `.env`.
- `rides/templates/site/` — the static site templates (`index.html`, `detail.html`,
  `base.html`). `rides/static_src/` — CSS + the client-side search/filter/sort JS,
  copied verbatim into the build (no bundler).
- `docs/` — generated output, committed directly (GitHub Pages branch publishing only
  supports `/` or `/docs` as source, hence no `/output` folder in prod). Don't hand-edit
  files here — they're overwritten by `build_site`. `preview/` and `output/` in the repo
  root are ad hoc local build targets, not part of the deploy path.

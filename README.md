# Gravelle Estrie — ride tracker

A consolidated catalogue of the [Gravelle Estrie](https://www.facebook.com/groups/388978452640649/events)
cycling club's rides. A small Django back-office (admin console + Postgres)
imports rides from Strava and RideWithGPS, renders map thumbnails, and
generates a **static website** you can publish on GitHub Pages.

- **Search** by name and start city
- **Filter** by distance and elevation gain
- **Ride cards** with a baked map thumbnail
- **Detail page** per ride: RideWithGPS or Strava route embed when available,
  full specs, links to Strava & RideWithGPS, and a downloadable GPX file for
  Garmin/manual import
- **Ravitos** on detail pages when a configured grocery/convenience stop is
  near the route

## How it works

```
Strava / RideWithGPS ──import──▶  Django + Postgres  ──build_site──▶  docs/ (static, committed)
                                  (admin console)        thumbnails baked from route geometry
```

The database and admin are **development/back-office only** — GitHub Pages only
ever serves the generated static files in `docs/`. Map thumbnails are
pre-rendered PNGs (OpenStreetMap tiles + the route line). Detail pages prefer
the RideWithGPS embed, then the official Strava route embed for Strava-only
routes, and fall back to the static thumbnail when no interactive embed is
available.

## Requirements

- Docker + Docker Compose (recommended), **or**
- Python 3.11+ and PostgreSQL for a local (non-container) setup. Local
  development without a `DATABASE_URL` falls back to SQLite automatically.

## Quick start with Docker

Brings up Postgres + the Django admin console, and creates a first admin user
(`admin` / `admin` by default — override with `DJANGO_SUPERUSER_*`):

```bash
docker compose up --build
# admin console: http://localhost:8000/admin/
```

Run the data + build commands against the same stack:

```bash
docker compose run --rm web python manage.py seed_demo          # demo rides
docker compose run --rm web python manage.py import             # bulk import (RideWithGPS + Strava)
docker compose run --rm web python manage.py build_site         # -> ./docs

docker compose run --rm web python manage.py import_strava
```

`./docs` and uploaded thumbnails are mounted as volumes, so the generated
static site appears in `docs/` on your host and can be committed. Put API
credentials and secrets in a `.env` file (see
`.env.example`); Compose reads it automatically for Compose variable
substitution — but note that `.env` is **not** copied or mounted into the
container itself (see `.dockerignore`), so only values explicitly passed
through `docker-compose.yml` or provided with `docker compose run -e ...` affect
commands run inside the container.

⚠️ **Always run `build_site` the same way you ran `import`.** Each reads
whatever database it's configured for — if you imported via
`docker compose run` (Postgres, inside the stack) but then run
`python manage.py build_site` locally with the venv activated, it'll silently
read your local SQLite file instead (likely just demo data) and you'll get a
site with the wrong rides. Keep both steps either inside Docker, or set
`DATABASE_URL` in your local `.env` to point at the same Postgres
(`postgres://gravelle:gravelle@localhost:5432/gravelle_estrie` — the `db`
service already exposes 5432 to the host) so local commands see the same data.

### Previewing the generated static site

An opt-in `preview` profile serves `./docs` at the domain root with nginx,
matching the custom-domain GitHub Pages setup (so it won't start with a plain
`docker compose up`):

```bash
docker compose --profile preview up preview
# open http://localhost:8080/
```

Re-run `build_site` and refresh the page to see changes — nginx reads
straight from the mounted `docs/` directory.

## Setup (without Docker)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then edit as needed

python manage.py migrate
python manage.py createsuperuser
```

### Run the admin console

```bash
python manage.py runserver
# open http://127.0.0.1:8000/admin/
```

Add, edit, publish/unpublish rides, and regenerate thumbnails from the admin.

### Seed demo data (optional)

No credentials needed — creates four sample Estrie rides with real map thumbnails:

```bash
python manage.py seed_demo
```

## Importing rides

Both sources import **routes/courses** (reusable planned rides), not
individually recorded activities — Strava's `GET /athlete/activities` and
RideWithGPS's "trips" are deliberately not used. This is what the club
actually wants published (the course, not a specific member's logged ride).

**RideWithGPS is the primary, bulk source** — its API can list every route
for a given user with normal pagination, scaling to hundreds of routes with
zero manual work. If a RideWithGPS route lives outside that account, add its
numeric id to `RWGPS_EXTRA_ROUTE_IDS`; those routes are fetched directly in
addition to `RWGPS_USER_ID`'s bulk list. **Strava is secondary and optional**:
its public API has no way to list another athlete's routes at all (see "Why
Strava can't be bulk-imported" below), so it only ever imports whatever
handful of route ids you've manually added to `STRAVA_ROUTE_IDS` — useful for
linking a Strava URL onto a few specific rides, not for getting everything.
If you don't care about Strava links, leave `STRAVA_ROUTE_IDS` empty and
RideWithGPS alone covers the whole import.

### Setup

1. **RideWithGPS** (do this one — it's what actually gets you all the
   routes): request an API key at <https://ridewithgps.com/api>. Fill
   `RWGPS_API_KEY` (and `RWGPS_AUTH_TOKEN` for private routes) in `.env`.
   `RWGPS_USER_ID` defaults to the club user from the spec. Add
   comma-separated route ids to `RWGPS_EXTRA_ROUTE_IDS` for routes that should
   be imported even though they are not listed under that user.
2. **Strava** (optional): create an API application at
   <https://www.strava.com/settings/api> to get a client id/secret. Put
   `STRAVA_CLIENT_ID` and `STRAVA_CLIENT_SECRET` in `.env`, then run the
   one-time OAuth consent helper:

   ```bash
   python manage.py strava_auth
   ```

   This opens a browser to Strava's consent screen (only needs the basic
   `read` scope), runs a small local server to catch the redirect, exchanges
   the code for tokens, and writes `STRAVA_REFRESH_TOKEN` straight into
   `.env`. Any Strava account can authorize this. Pass `--no-browser` to just
   print the URL (e.g. over SSH).

   Then, if you want any Strava links at all, collect route ids into
   `STRAVA_ROUTE_IDS` (comma-separated) in `.env` — one at a time, since
   there's no bulk way to do this (see below): open each route in the Strava
   app and tap its share icon, or open it on strava.com, and copy the numeric
   id from the `strava.com/routes/<id>` link.

### Why Strava can't be bulk-imported

Strava's route *listing* endpoint (`GET /athletes/{id}/routes`) only ever
returns routes for whichever athlete is currently authenticated — passing any
other athlete's id returns `403 Forbidden`, even for public routes, and
there's no supported way around that for a third-party app. The Strava
mobile/web app can browse another athlete's public routes tab because it
uses Strava's private, unpublished API — not available to OAuth apps, and
not worth reverse-engineering past its certificate pinning (Charles Proxy
and friends won't get you there either). So there is no way to auto-discover
an arbitrary athlete's routes through the public API — `import_strava`
instead fetches each route individually by id (`GET /routes/{id}`, which
*does* work for public routes regardless of who authorized the token — the
same mechanism behind Strava's shareable route links) from the explicit list
in `STRAVA_ROUTE_IDS`. This doesn't scale past a handful of manually-picked
routes, which is why RideWithGPS is the source to rely on for the full set.

### Running the import

```bash
python manage.py import
```

`import_ridewithgps` runs first (bulk-creates a `Ride` per new public
RideWithGPS route from `RWGPS_USER_ID`, plus any route ids in
`RWGPS_EXTRA_ROUTE_IDS`), then `import_strava` matches each new
`STRAVA_ROUTE_IDS` route onto an existing RideWithGPS-sourced ride (same name,
similar distance) and merges its link in — or creates a new ride if genuinely
not already present. Both steps are incremental by default: they still read
the source route lists, but skip detail fetches for source ids already present
locally, which keeps repeat runs lighter on API calls. Pass `--full` to fetch
and update every configured route, including already-imported ones. Pass
`--no-thumbnails` to skip tile downloads.

You can also run either step on its own — `python manage.py import_ridewithgps`
or `python manage.py import_strava` — each supports `--full` and a
`--require-<other>-match` flag (`--require-strava-match` /
`--require-rwgps-match`) if you want a strict "only enrich, never create a new
ride" run instead of the default (create a standalone ride when nothing
matches).

Both steps log to stdout as they run (each ride created/updated/merged/
skipped, API calls, thumbnail failures) — useful when running non-interactively
(cron, `docker compose run`). Set `DJANGO_LOG_LEVEL=DEBUG` in `.env` for
per-API-request detail, or `WARNING` to quiet it down to just problems.

Both importers only pull **cycling** routes that start in Quebec, skipping
other sports on the same account and routes whose first point is outside
Quebec:

- RideWithGPS: filters on the route's `activity_types` — anything tagged
  `cycling:*` (road, gravel, mountain, commute) is imported; `walking:*`,
  `running:*`, `motorcycling:*`, etc. are skipped. Routes with no
  `activity_types` at all (older routes may predate the field) are assumed to
  be cycling. This applies to both the user-list routes and direct ids from
  `RWGPS_EXTRA_ROUTE_IDS`. The start province comes from RideWithGPS's
  `administrative_area` when available; otherwise the first track point is
  used. Known source-data mistakes can be suppressed with
  `RWGPS_EXCLUDED_ROUTE_IDS` (comma-separated RideWithGPS route ids); by
  default this excludes the two "Course" running routes currently tagged
  `cycling:gravel` by RideWithGPS.
- Strava: route `type` values `1` and `6` are treated as cycling routes
  (Strava returns `6` for at least some gravel ride routes); known non-cycling
  values such as `2` (Run) are skipped. Private routes are also explicitly
  excluded, regardless of scope, and the decoded route polyline must start in
  Quebec. Strava route payloads do not include a start locality, so the import
  and static-site build infer a start city from the first route point using a
  small local list of common club departure hubs.

### Local ride images

Ride photos can live in the local, git-ignored `images/` directory:

```text
images/<ride id>/photo.jpg
images/<ride id>/another-photo.webp
```

The lookup uses `rwgps_route_id` first (for example `images/55674259/`), then
`strava_activity_id`, `external_id`, the Django row id, and finally the slug.
The admin ride form shows any matching local images and serves them through a
protected admin URL. `build_site` copies them into
`docs/assets/ride-images/<slug>/`; on the detail page they appear under the
distance/elevation/city stats, open in a popover, and the first image becomes
a subtle transparent page background. Rides without local photos use
`rides/static_src/img/default-ride-cover.jpg` as the same transparent
background.

### GPX / Garmin downloads

`build_site` writes a GPX Track file for every published ride with at least
two geometry points:

```text
docs/assets/gpx/<slug>.gpx
```

The detail page exposes it as the "Télécharger GPX" button beside the Strava
and RideWithGPS links. The file is generated from the locally stored
`Ride.geometry` instead of relying on a RideWithGPS export URL: the public
RideWithGPS API used here is JSON-oriented, while RideWithGPS's richer
FIT/TCX/GPX exports are primarily a product/UI feature. These generated GPX
files are breadcrumb tracks (`lat/lng` points), suitable for manual Garmin
import; they do not include RideWithGPS cue sheets or turn-by-turn metadata.

### Ravitos

Known grocery stores, cafés, dépanneurs, and parking spots can be listed in
`.env`. `build_site` automatically shows ravitos within `RAVITO_RADIUS_M` of
each route on the ride detail page, once the closest point on the route is far
enough from the start. Parking spots use the same entry format, but are matched
against the route's first GPS point.

```env
RAVITO_POINTS=https://maps.app.goo.gl/bCT3vqf9aJ38ohCo9;Nom court|https://www.google.com/maps/place/...;Nom manuel|45.123456|-72.123456
RAVITO_RADIUS_M=500
RAVITO_MIN_ROUTE_DISTANCE_M=30000
RAVITO_ENDPOINT_EXCLUSION_RADIUS_M=3000
PARKING_POINTS=Stationnement|https://maps.app.goo.gl/...;Stationnement manuel|45.123456|-72.123456
PARKING_RADIUS_M=500
GOOGLE_MAPS_URL_CACHE_PATH=.cache/google-maps-url-cache.json
```

Entries can be separated by `;`, newlines, or commas before another Google
Maps URL. A Google Maps URL can be used alone; if it has a long Google place
name, prefix it with `Nom à afficher|`. Coordinate entries also work as
`Nom|latitude|longitude`. Short `maps.app.goo.gl` links are resolved during
`build_site`, then cached locally in `GOOGLE_MAPS_URL_CACHE_PATH` so later
builds do not need to hit Google again for the same links. Delete that cache
file to force a refresh; set `GOOGLE_MAPS_URL_CACHE_PATH=off` to disable it.
Entries that cannot be resolved to coordinates are skipped and are not cached.
By default, a ravito must be at least 30 km into the route and more than 3 km
from the route's start or finish point.

Add `?admin=true` to the static site index URL to reveal admin-only filters
for rides without any detected ravito or parking:

```text
https://www.gravelleestrie.com/?admin=true
http://localhost:8080/?admin=true
```

### Cross-source matching

A Strava route is matched onto an existing RideWithGPS-sourced ride by the
**same name** (case- and whitespace-insensitive) and a distance within 15%
(tolerant of the two platforms measuring slightly differently) — not by
date. Neither source's route object carries the day the club actually rode
it (at most a route *creation* date), so `ride_date` is left blank on
auto-imported rides; fill it in manually in the admin when known. When
matched, the two links live on the same `Ride` — its detail page shows both
"Voir sur Strava" and "Voir sur RideWithGPS" buttons, and the admin's "Liens"
column shows clickable links to whichever are present, so you can see what
matched (or didn't) after an import.

This relies on rides being named consistently across both platforms. It's a
heuristic, not exact matching — two unrelated rides with the same name and a
similar distance could incorrectly merge; check the admin's "Liens" column
after importing both sources for the first time.

## Building the static site
```bash
docker compose run --rm web python manage.py build_site
```

(or `docker compose exec web ...` if the stack is already up with `docker
compose up`).

`SITE_BASE_PATH` defaults to an empty string, matching the custom domain
`https://www.gravelleestrie.com/`. Set it only if the site is served from a
subdirectory, for example `/GravelleEstrie` for project-page hosting.
`SITE_CUSTOM_DOMAIN` defaults to `www.gravelleestrie.com`; `build_site` writes
that value to `docs/CNAME` so GitHub Pages keeps the custom domain after each
generated build.

Preview locally:

```bash
python -m http.server 8765 --directory .
# open http://127.0.0.1:8765/
```

If you want a throwaway preview in a separate directory:

```bash
python manage.py build_site --output preview
python -m http.server 8765 --directory preview
```

## Publishing to GitHub Pages

There is no GitHub Actions deploy workflow. The generated static site is
committed directly in `docs/`, because GitHub Pages branch publishing only
supports `/ (root)` or `/docs` as the source folder:

1. Import or edit rides, then build the static site:

   ```bash
   docker compose run --rm web python manage.py build_site
   ```

2. Commit the resulting `docs/` changes and push.
3. In the repo settings, use **Pages → Source → Deploy from a branch** and
   publish `main` from `/docs`. The site is then available under
   `https://www.gravelleestrie.com/`.

Thumbnails are copied into `docs/assets/thumbs/` and the custom domain is
written to `docs/CNAME` by `build_site`, so both are committed with the static
HTML/CSS/JS.

## Tests

```bash
python manage.py test rides
```

## Project layout

```
config/            Django project (settings, urls, wsgi)
rides/
  models.py        Ride model
  admin.py         Admin console + import/thumbnail actions
  services/        Strava & RideWithGPS clients, geometry, thumbnails, importer
  management/commands/
    strava_auth.py      One-time Strava OAuth helper, writes STRAVA_REFRESH_TOKEN to .env
    import.py           Runs import_ridewithgps then import_strava in order
    import_strava.py
    import_ridewithgps.py
    render_thumbnails.py
    build_site.py       Static site generator
    seed_demo.py
  templates/site/  Static site templates (index, detail)
  static_src/      CSS + search/filter JS copied into the build
  fixtures/        rides.json (optional export/import fixture)
docs/                  Generated static site committed for GitHub Pages
Dockerfile             Django admin/back-office image
docker-compose.yml     Postgres + web services
entrypoint.sh          Container startup: migrate, collectstatic, superuser
```

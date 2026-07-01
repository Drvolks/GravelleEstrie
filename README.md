# Gravelle Estrie — ride tracker

A consolidated catalogue of the [Gravelle Estrie](https://www.facebook.com/groups/388978452640649/events)
cycling club's rides. A small Django back-office (admin console + Postgres)
imports rides from Strava and RideWithGPS, renders map thumbnails, and
generates a **static website** you can publish on GitHub Pages.

- **Search** by name and start city
- **Filter** by distance and elevation gain
- **Ride cards** with a baked map thumbnail
- **Detail page** per ride: RideWithGPS map embed when available, full specs,
  links to Strava & RideWithGPS
- _Post-MVP:_ a "Send to Garmin" button (placeholder left in the detail template)

## How it works

```
Strava / RideWithGPS ──import──▶  Django + Postgres  ──build_site──▶  docs/ (static, committed)
                                  (admin console)        thumbnails baked from route geometry
```

The database and admin are **development/back-office only** — GitHub Pages only
ever serves the generated static files in `docs/`. Map thumbnails are
pre-rendered PNGs (OpenStreetMap tiles + the route line), so the published site
needs no map API keys or JavaScript maps.

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

An opt-in `preview` profile serves `./docs` under the
`/GravelleEstrie/` path with nginx, exactly as GitHub Pages would (so it won't
start with a plain `docker compose up`):

```bash
docker compose --profile preview up preview
# open http://localhost:8080/GravelleEstrie/
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
zero manual work. **Strava is secondary and optional**: its public API has no
way to list another athlete's routes at all (see "Why Strava can't be
bulk-imported" below), so it only ever imports whatever handful of route ids
you've manually added to `STRAVA_ROUTE_IDS` — useful for linking a Strava URL
onto a few specific rides, not for getting everything. If you don't care
about Strava links, leave `STRAVA_ROUTE_IDS` empty and RideWithGPS alone
covers the whole import.

### Setup

1. **RideWithGPS** (do this one — it's what actually gets you all the
   routes): request an API key at <https://ridewithgps.com/api>. Fill
   `RWGPS_API_KEY` (and `RWGPS_AUTH_TOKEN` for private routes) in `.env`.
   `RWGPS_USER_ID` defaults to the club user from the spec.
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
RideWithGPS route), then `import_strava` matches each new `STRAVA_ROUTE_IDS`
route onto an existing RideWithGPS-sourced ride (same name, similar distance)
and merges its link in — or creates a new ride if genuinely not already
present. Both steps are incremental by default: they still read the source
route lists, but skip detail fetches for source ids already present locally,
which keeps repeat runs lighter on API calls. Pass `--full` to fetch and
update every configured route, including already-imported ones. Pass
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

Both importers only pull **cycling** routes, skipping other sports on the
same account:

- RideWithGPS: filters on the route's `activity_types` — anything tagged
  `cycling:*` (road, gravel, mountain, commute) is imported; `walking:*`,
  `running:*`, `motorcycling:*`, etc. are skipped. Routes with no
  `activity_types` at all (older routes may predate the field) are assumed to
  be cycling. Known source-data mistakes can be suppressed with
  `RWGPS_EXCLUDED_ROUTE_IDS` (comma-separated RideWithGPS route ids); by
  default this excludes the two "Course" running routes currently tagged
  `cycling:gravel` by RideWithGPS.
- Strava: routes have a `type` of `1` (Ride) or `2` (Run) — only `1` is
  imported. Private routes are also explicitly excluded, regardless of scope.

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

`SITE_BASE_PATH` defaults to `/GravelleEstrie`, matching the GitHub Pages URL
for this repository. Override it only if the site is served somewhere else:
set it to an empty string for a domain root, or another leading-slash path for
another subdirectory. For Docker runs, pass overrides explicitly, for example
`docker compose run --rm -e SITE_BASE_PATH= web python manage.py build_site`.

Preview locally:

```bash
python -m http.server 8765 --directory .
# open http://127.0.0.1:8765/GravelleEstrie/
```

If you want a root-relative throwaway preview, clear `SITE_BASE_PATH` and build
to `preview/`:

```bash
SITE_BASE_PATH= python manage.py build_site --output preview
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
   `/GravelleEstrie/`.

Thumbnails are copied into `docs/assets/thumbs/` by `build_site`, so
they are committed with the static HTML/CSS/JS.

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

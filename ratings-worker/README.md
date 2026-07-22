# Gravelle Estrie ratings Worker

Cloudflare Worker API for anonymous ride star ratings. The static site calls
this API; the Worker validates Turnstile, applies rate limits, hashes visitor
signals, and stores votes in Cloudflare D1.

## API

```text
POST /api/ratings
GET  /api/ratings/:ride_slug
POST /api/ratings/:ride_slug
```

`POST /api/ratings` accepts `{ "ride_slugs": ["ride-a", "ride-b"] }` and
returns summaries for the static index page. Missing rides return zero votes.

`GET /api/ratings/:ride_slug` returns one summary. `POST
/api/ratings/:ride_slug` records a Turnstile-verified vote.

## Setup

Install dependencies:

```bash
npm install
```

Create the D1 database:

```bash
npx wrangler d1 create gravelleestrie-ratings
```

Copy the returned `database_id` into `wrangler.toml`, replacing
`replace-with-cloudflare-d1-database-id`.

Apply the schema:

```bash
npm run db:migrate:remote
```

Create Worker secrets:

```bash
npx wrangler secret put TURNSTILE_SECRET_KEY
npx wrangler secret put RATING_HASH_SALT
```

`TURNSTILE_SECRET_KEY` comes from the Cloudflare Turnstile widget. Use a long
random value for `RATING_HASH_SALT`.

Deploy:

```bash
npm run deploy
```

## Updating Allowed Origins

`ALLOWED_ORIGINS` is a comma-separated list of exact browser origins allowed by
CORS. Origins include the scheme, so `http://www.gravelleestrie.com` and
`https://www.gravelleestrie.com` are different values. No trailing slash.

To update the deployed Cloudflare Worker configuration:

1. Edit `ALLOWED_ORIGINS` in `wrangler.toml` under `[vars]`.
2. Keep `DEFAULT_ALLOWED_ORIGINS` in `src/index.js` in sync as the code
   fallback.
3. Add or update a preflight test in `test/batch.test.mjs`.
4. Run the tests:

```bash
npm test
```

5. Deploy the Worker:

```bash
npm run deploy
```

6. Verify the origin you added with a preflight request:

```bash
curl -i -X OPTIONS "https://gravelleestrie-ratings.fleece-00-clad.workers.dev/api/ratings" \
  -H "Origin: http://localhost:8080" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type,accept"
```

Expected result: `HTTP/2 204` and
`access-control-allow-origin: <the exact Origin header>`. If a newly deployed
origin still returns `403` immediately after deploy, wait a few seconds and
retry once; Cloudflare propagation can briefly serve the previous version.

## Static Site Configuration

Set these values in the Django `.env` before running `build_site`:

```bash
RATINGS_API_URL=https://gravelleestrie-ratings.<account>.workers.dev
TURNSTILE_SITE_KEY=<public Turnstile site key>
```

Then rebuild and publish the static site:

```bash
python manage.py build_site
```

## Local Development

Apply the schema locally and start Wrangler:

```bash
npm run db:migrate:local
npm run dev
```

Add the local Worker URL to `RATINGS_API_URL` when previewing the static site.
Production `wrangler.toml` allows public Gravelle Estrie origins plus
`http://localhost:8080` for the Docker static-site preview. Additional local
Worker development origins are kept in `.dev.vars`, which Wrangler loads during
`npm run dev` and which is ignored by Git. Use `.dev.vars.example` as the
template if you need to recreate it.

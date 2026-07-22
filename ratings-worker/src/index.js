const DEFAULT_ALLOWED_ORIGINS = [
  "https://gravelleestrie.com",
  "https://www.gravelleestrie.com",
];

const RATING_COLLECTION_ROUTE = /^\/api\/ratings$/;
const RIDE_ROUTE = /^\/api\/ratings\/([a-z0-9-]+)$/;
const VOTER_ID_RE = /^[A-Za-z0-9:_-]{8,160}$/;
const MAX_BATCH_SLUGS = 500;
const D1_BATCH_CHUNK_SIZE = 50;
const MAX_IP_VOTES_PER_HOUR = 5;
const MAX_VOTER_VOTES_PER_HOUR = 10;

export default {
  async fetch(request, env) {
    try {
      return await handleRequest(request, env);
    } catch (error) {
      console.error(error);
      return jsonResponse(request, env, { error: "Erreur interne." }, 500);
    }
  },
};

export async function handleRequest(request, env) {
  const url = new URL(request.url);
  const isCollectionRoute = RATING_COLLECTION_ROUTE.test(url.pathname);
  const match = url.pathname.match(RIDE_ROUTE);

  if (request.method === "OPTIONS") {
    return handleOptions(request, env);
  }

  if (!isCollectionRoute && !match) {
    return jsonResponse(request, env, { error: "Route introuvable." }, 404);
  }

  if (!isAllowedOrigin(request, env)) {
    return jsonResponse(request, env, { error: "Origine non autorisee." }, 403);
  }

  if (isCollectionRoute) {
    if (request.method !== "POST") {
      return jsonResponse(request, env, { error: "Methode non supportee." }, 405, {
        Allow: "POST, OPTIONS",
      });
    }
    return getBatchSummaries(request, env);
  }

  const slug = match[1];
  if (!isValidRideSlug(slug)) {
    return jsonResponse(request, env, { error: "Identifiant de sortie invalide." }, 400);
  }

  if (request.method === "GET") {
    return jsonResponse(request, env, await getSummary(env, slug));
  }

  if (request.method === "POST") {
    return submitVote(request, env, slug);
  }

  return jsonResponse(request, env, { error: "Methode non supportee." }, 405, {
    Allow: "GET, POST, OPTIONS",
  });
}

async function getBatchSummaries(request, env) {
  const body = await parseJson(request);
  const slugs = Array.isArray(body.ride_slugs)
    ? Array.from(new Set(body.ride_slugs.map((slug) => String(slug || "").trim())))
    : [];

  if (!slugs.length || slugs.length > MAX_BATCH_SLUGS || slugs.some((slug) => !isValidRideSlug(slug))) {
    return jsonResponse(request, env, { error: "Liste de sorties invalide." }, 400);
  }

  const rows = [];
  for (let index = 0; index < slugs.length; index += D1_BATCH_CHUNK_SIZE) {
    const chunk = slugs.slice(index, index + D1_BATCH_CHUNK_SIZE);
    const placeholders = chunk.map((_, chunkIndex) => `?${chunkIndex + 1}`).join(", ");
    const result = await env.DB.prepare(
      `select ride_slug, vote_count, average_rating
       from ride_rating_summary
       where ride_slug in (${placeholders})`
    ).bind(...chunk).all();
    rows.push(...(result.results || []));
  }

  const summariesBySlug = new Map(
    rows.map((row) => [
      row.ride_slug,
      {
        ride_slug: row.ride_slug,
        vote_count: Number(row.vote_count || 0),
        average_rating: Number(Number(row.average_rating || 0).toFixed(2)),
      },
    ])
  );

  return jsonResponse(request, env, {
    summaries: slugs.map((slug) => (
      summariesBySlug.get(slug) || { ride_slug: slug, vote_count: 0, average_rating: 0 }
    )),
  });
}

function handleOptions(request, env) {
  if (!isAllowedOrigin(request, env)) {
    return new Response(null, { status: 403 });
  }
  return new Response(null, {
    status: 204,
    headers: corsHeaders(request, env, {
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Accept",
      "Access-Control-Max-Age": "86400",
    }),
  });
}

async function submitVote(request, env, slug) {
  const clientIp = request.headers.get("cf-connecting-ip") || "unknown";

  if (env.RATE_LIMITER) {
    const rate = await env.RATE_LIMITER.limit({ key: `${clientIp}:ratings` });
    if (!rate.success) {
      return jsonResponse(request, env, { error: "Trop de tentatives. Reessayez plus tard." }, 429);
    }
  }

  const body = await parseJson(request);
  const rating = Number(body.rating);
  const voterId = String(body.voter_id || "");
  const turnstileToken = String(body.turnstile_token || "");

  if (!Number.isInteger(rating) || rating < 1 || rating > 5) {
    return jsonResponse(request, env, { error: "La note doit etre entre 1 et 5." }, 400);
  }

  if (!VOTER_ID_RE.test(voterId)) {
    return jsonResponse(request, env, { error: "Identifiant de vote invalide." }, 400);
  }

  if (!env.RATING_HASH_SALT) {
    return jsonResponse(request, env, { error: "Configuration de vote incomplete." }, 500);
  }

  const turnstile = await verifyTurnstile(env, turnstileToken, clientIp);
  if (!turnstile.success) {
    console.warn("Turnstile verification failed", {
      errorCodes: turnstile.errorCodes,
      hostname: turnstile.hostname,
      action: turnstile.action,
    });
    return jsonResponse(
      request,
      env,
      {
        error: "Validation anti-robot refusee.",
        turnstile_error_codes: turnstile.errorCodes,
      },
      403
    );
  }

  const userAgent = (request.headers.get("user-agent") || "unknown").slice(0, 256);
  const voterHash = await sha256Hex(`${env.RATING_HASH_SALT}:voter:${voterId}`);
  const ipHash = await sha256Hex(`${env.RATING_HASH_SALT}:ip:${clientIp}`);
  const userAgentHash = await sha256Hex(`${env.RATING_HASH_SALT}:ua:${userAgent}`);

  const existing = await env.DB.prepare(
    "select id from ride_votes where ride_slug = ?1 and voter_hash = ?2 limit 1"
  ).bind(slug, voterHash).first();

  if (existing) {
    return jsonResponse(
      request,
      env,
      { error: "Vous avez deja vote pour cette sortie.", summary: await getSummary(env, slug) },
      409
    );
  }

  const ipVotes = await countRecentVotes(env, "ip_hash", ipHash);
  if (ipVotes >= MAX_IP_VOTES_PER_HOUR) {
    return jsonResponse(request, env, { error: "Trop de votes depuis cette connexion." }, 429);
  }

  const voterVotes = await countRecentVotes(env, "voter_hash", voterHash);
  if (voterVotes >= MAX_VOTER_VOTES_PER_HOUR) {
    return jsonResponse(request, env, { error: "Trop de votes recents." }, 429);
  }

  try {
    await env.DB.prepare(
      `insert into ride_votes (ride_slug, rating, voter_hash, ip_hash, user_agent_hash)
       values (?1, ?2, ?3, ?4, ?5)`
    ).bind(slug, rating, voterHash, ipHash, userAgentHash).run();
  } catch (error) {
    if (String(error && error.message).toLowerCase().includes("unique")) {
      return jsonResponse(
        request,
        env,
        { error: "Vous avez deja vote pour cette sortie.", summary: await getSummary(env, slug) },
        409
      );
    }
    throw error;
  }

  await env.DB.prepare(
    `insert into ride_rating_summary (ride_slug, vote_count, rating_sum, average_rating)
     values (?1, 1, ?2, ?2)
     on conflict(ride_slug) do update set
       vote_count = ride_rating_summary.vote_count + 1,
       rating_sum = ride_rating_summary.rating_sum + excluded.rating_sum,
       average_rating = (
         (ride_rating_summary.rating_sum + excluded.rating_sum) * 1.0 /
         (ride_rating_summary.vote_count + 1)
       ),
       updated_at = current_timestamp`
  ).bind(slug, rating).run();

  return jsonResponse(request, env, { summary: await getSummary(env, slug) }, 201);
}

async function parseJson(request) {
  try {
    return await request.json();
  } catch (_error) {
    return {};
  }
}

async function verifyTurnstile(env, token, remoteIp) {
  if (!env.TURNSTILE_SECRET_KEY || !token || token.length > 2048) {
    return { success: false, errorCodes: ["missing-local-input"] };
  }

  const formData = new FormData();
  formData.append("secret", env.TURNSTILE_SECRET_KEY);
  formData.append("response", token);
  formData.append("remoteip", remoteIp);
  formData.append("idempotency_key", crypto.randomUUID());

  const response = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    body: formData,
  });
  const result = await response.json();
  const errorCodes = result["error-codes"] || [];

  if (!result.success) {
    return {
      success: false,
      errorCodes,
      hostname: result.hostname,
      action: result.action,
    };
  }
  if (result.action && result.action !== "ride_rating") {
    return {
      success: false,
      errorCodes: ["action-mismatch"],
      hostname: result.hostname,
      action: result.action,
    };
  }

  return { success: true, hostname: result.hostname, action: result.action };
}

async function countRecentVotes(env, column, hash) {
  const row = await env.DB.prepare(
    `select count(*) as count
     from ride_votes
     where ${column} = ?1
       and created_at >= datetime('now', '-1 hour')`
  ).bind(hash).first();
  return Number(row && row.count ? row.count : 0);
}

async function getSummary(env, slug) {
  const row = await env.DB.prepare(
    `select ride_slug, vote_count, average_rating
     from ride_rating_summary
     where ride_slug = ?1`
  ).bind(slug).first();

  if (!row) {
    return { ride_slug: slug, vote_count: 0, average_rating: 0 };
  }

  return {
    ride_slug: slug,
    vote_count: Number(row.vote_count || 0),
    average_rating: Number(Number(row.average_rating || 0).toFixed(2)),
  };
}

async function sha256Hex(value) {
  const data = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function jsonResponse(request, env, body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: corsHeaders(request, env, {
      "Content-Type": "application/json; charset=utf-8",
      ...headers,
    }),
  });
}

function corsHeaders(request, env, headers = {}) {
  const origin = request.headers.get("origin");
  if (origin && isAllowedOrigin(request, env)) {
    headers["Access-Control-Allow-Origin"] = origin;
    headers.Vary = "Origin";
  }
  return headers;
}

export function isAllowedOrigin(request, env) {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  return parseAllowedOrigins(env).includes(origin);
}

export function parseAllowedOrigins(env) {
  const raw = env.ALLOWED_ORIGINS || DEFAULT_ALLOWED_ORIGINS.join(",");
  return raw
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);
}

export function isValidRideSlug(slug) {
  return /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug);
}

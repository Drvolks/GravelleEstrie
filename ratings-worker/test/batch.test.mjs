import test from "node:test";
import assert from "node:assert/strict";

import { handleRequest } from "../src/index.js";

function mockEnv(rows) {
  const queries = [];
  return {
    ALLOWED_ORIGINS: [
      "http://gravelleestrie.com",
      "http://www.gravelleestrie.com",
      "https://gravelleestrie.com",
      "https://www.gravelleestrie.com",
    ].join(","),
    queries,
    DB: {
      prepare(sql) {
        return {
          bind(...slugs) {
            return {
              async all() {
                assert.match(sql, /ride_rating_summary/);
                queries.push(slugs);
                return {
                  results: rows.filter((row) => slugs.includes(row.ride_slug)),
                };
              },
            };
          },
        };
      },
    },
  };
}

test("returns batch rating summaries with zero defaults", async () => {
  const request = new Request("https://worker.example/api/ratings", {
    method: "POST",
    headers: {
      Origin: "https://www.gravelleestrie.com",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ride_slugs: ["ride-a", "ride-b"] }),
  });

  const response = await handleRequest(request, mockEnv([
    { ride_slug: "ride-a", vote_count: 3, average_rating: 4.666 },
  ]));
  const payload = await response.json();

  assert.equal(response.status, 200);
  assert.deepEqual(payload, {
    summaries: [
      { ride_slug: "ride-a", vote_count: 3, average_rating: 4.67 },
      { ride_slug: "ride-b", vote_count: 0, average_rating: 0 },
    ],
  });
});

test("chunks large batch rating summary reads", async () => {
  const slugs = Array.from({ length: 120 }, (_, index) => `ride-${index + 1}`);
  const env = mockEnv([
    { ride_slug: "ride-120", vote_count: 1, average_rating: 5 },
  ]);
  const request = new Request("https://worker.example/api/ratings", {
    method: "POST",
    headers: {
      Origin: "https://www.gravelleestrie.com",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ride_slugs: slugs }),
  });

  const response = await handleRequest(request, env);
  const payload = await response.json();

  assert.equal(response.status, 200);
  assert.deepEqual(env.queries.map((query) => query.length), [50, 50, 20]);
  assert.deepEqual(payload.summaries.at(-1), {
    ride_slug: "ride-120",
    vote_count: 1,
    average_rating: 5,
  });
});

test("rejects invalid batch slugs", async () => {
  const request = new Request("https://worker.example/api/ratings", {
    method: "POST",
    headers: {
      Origin: "https://www.gravelleestrie.com",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ride_slugs: ["../ride"] }),
  });

  const response = await handleRequest(request, mockEnv([]));
  assert.equal(response.status, 400);
});

test("allows preflight from the apex production origin", async () => {
  const request = new Request("https://worker.example/api/ratings", {
    method: "OPTIONS",
    headers: {
      Origin: "https://gravelleestrie.com",
      "Access-Control-Request-Method": "POST",
      "Access-Control-Request-Headers": "content-type,accept",
    },
  });

  const response = await handleRequest(request, mockEnv([]));

  assert.equal(response.status, 204);
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), "https://gravelleestrie.com");
  assert.match(response.headers.get("Access-Control-Allow-Methods") || "", /POST/);
  assert.match(response.headers.get("Access-Control-Allow-Headers") || "", /Content-Type/);
});

test("allows preflight from the http www production origin", async () => {
  const request = new Request("https://worker.example/api/ratings", {
    method: "OPTIONS",
    headers: {
      Origin: "http://www.gravelleestrie.com",
      "Access-Control-Request-Method": "POST",
      "Access-Control-Request-Headers": "content-type,accept",
    },
  });

  const response = await handleRequest(request, mockEnv([]));

  assert.equal(response.status, 204);
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), "http://www.gravelleestrie.com");
});

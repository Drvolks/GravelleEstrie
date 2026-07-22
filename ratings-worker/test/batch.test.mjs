import test from "node:test";
import assert from "node:assert/strict";

import { handleRequest } from "../src/index.js";

function mockEnv(rows) {
  return {
    ALLOWED_ORIGINS: "https://www.gravelleestrie.com",
    DB: {
      prepare(sql) {
        return {
          bind(...slugs) {
            return {
              async all() {
                assert.match(sql, /ride_rating_summary/);
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

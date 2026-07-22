import test from "node:test";
import assert from "node:assert/strict";

import { isAllowedOrigin, isValidRideSlug, parseAllowedOrigins } from "../src/index.js";

test("validates ride slugs", () => {
  assert.equal(isValidRideSlug("gravelle-du-mont-orford"), true);
  assert.equal(isValidRideSlug("ride-2026"), true);
  assert.equal(isValidRideSlug("Ride-2026"), false);
  assert.equal(isValidRideSlug("../ride"), false);
  assert.equal(isValidRideSlug("ride--double"), false);
});

test("parses configured allowed origins", () => {
  const env = { ALLOWED_ORIGINS: "https://www.example.com, http://localhost:8080 " };
  assert.deepEqual(parseAllowedOrigins(env), [
    "https://www.example.com",
    "http://localhost:8080",
  ]);
});

test("allows only configured browser origins", () => {
  const env = { ALLOWED_ORIGINS: "https://example.com,https://www.example.com" };
  const allowedRoot = new Request("https://worker.example/api/ratings/ride-a", {
    headers: { Origin: "https://example.com" },
  });
  const allowed = new Request("https://worker.example/api/ratings/ride-a", {
    headers: { Origin: "https://www.example.com" },
  });
  const denied = new Request("https://worker.example/api/ratings/ride-a", {
    headers: { Origin: "https://evil.example" },
  });

  assert.equal(isAllowedOrigin(allowedRoot, env), true);
  assert.equal(isAllowedOrigin(allowed, env), true);
  assert.equal(isAllowedOrigin(denied, env), false);
});

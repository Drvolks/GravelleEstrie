import test from "node:test";
import assert from "node:assert/strict";

import { handleRequest } from "../src/index.js";

function mockEnv() {
  const db = new MockDb();
  return {
    ALLOWED_ORIGINS: "https://www.gravelleestrie.com",
    DB: db,
    RATING_HASH_SALT: "test-salt",
    TURNSTILE_SECRET_KEY: "test-secret",
    db,
  };
}

function voteRequest(rating, voterId = "voter-1234") {
  return new Request("https://worker.example/api/ratings/ride-a", {
    method: "POST",
    headers: {
      Origin: "https://www.gravelleestrie.com",
      "Content-Type": "application/json",
      "User-Agent": "node-test",
      "cf-connecting-ip": "203.0.113.10",
    },
    body: JSON.stringify({
      rating,
      voter_id: voterId,
      turnstile_token: "token",
    }),
  });
}

function summaryRequest(voterId = "") {
  const url = new URL("https://worker.example/api/ratings/ride-a");
  if (voterId) url.searchParams.set("voter_id", voterId);
  return new Request(url.toString(), {
    headers: {
      Origin: "https://www.gravelleestrie.com",
    },
  });
}

class MockDb {
  constructor() {
    this.nextVoteId = 1;
    this.votes = [];
    this.summaries = new Map();
  }

  prepare(sql) {
    const db = this;
    return {
      bind(...args) {
        return {
          all: () => db.all(sql, args),
          first: () => db.first(sql, args),
          run: () => db.run(sql, args),
        };
      },
    };
  }

  async all() {
    return { results: [] };
  }

  async first(sql, args) {
    if (/select id, rating from ride_votes/.test(sql)) {
      const [slug, voterHash] = args;
      const vote = this.votes.find((row) => row.ride_slug === slug && row.voter_hash === voterHash);
      return vote ? { id: vote.id, rating: vote.rating } : null;
    }

    if (/select rating from ride_votes/.test(sql)) {
      const [slug, voterHash] = args;
      const vote = this.votes.find((row) => row.ride_slug === slug && row.voter_hash === voterHash);
      return vote ? { rating: vote.rating } : null;
    }

    if (/select count\(\*\) as count/.test(sql)) {
      return { count: 0 };
    }

    if (/from ride_rating_summary/.test(sql)) {
      const summary = this.summaries.get(args[0]);
      return summary
        ? {
            ride_slug: args[0],
            vote_count: summary.vote_count,
            average_rating: summary.rating_sum / summary.vote_count,
          }
        : null;
    }

    throw new Error(`Unexpected first query: ${sql}`);
  }

  async run(sql, args) {
    if (/insert into ride_votes/.test(sql)) {
      const [slug, rating, voterHash, ipHash, userAgentHash] = args;
      if (this.votes.some((row) => row.ride_slug === slug && row.voter_hash === voterHash)) {
        throw new Error("UNIQUE constraint failed: ride_votes.ride_slug, ride_votes.voter_hash");
      }
      this.votes.push({
        id: this.nextVoteId++,
        ride_slug: slug,
        rating,
        voter_hash: voterHash,
        ip_hash: ipHash,
        user_agent_hash: userAgentHash,
      });
      return { success: true };
    }

    if (/update ride_votes/.test(sql)) {
      const [rating, ipHash, userAgentHash, id] = args;
      const vote = this.votes.find((row) => row.id === id);
      assert.ok(vote, "expected vote to update");
      vote.rating = rating;
      vote.ip_hash = ipHash;
      vote.user_agent_hash = userAgentHash;
      return { success: true };
    }

    if (/insert into ride_rating_summary/.test(sql)) {
      const [slug, rating] = args;
      const summary = this.summaries.get(slug) || { vote_count: 0, rating_sum: 0 };
      summary.vote_count += 1;
      summary.rating_sum += rating;
      this.summaries.set(slug, summary);
      return { success: true };
    }

    if (/update ride_rating_summary/.test(sql)) {
      const [delta, slug] = args;
      const summary = this.summaries.get(slug);
      assert.ok(summary, "expected summary to update");
      summary.rating_sum += delta;
      return { success: true };
    }

    throw new Error(`Unexpected run query: ${sql}`);
  }
}

test("updates an existing vote instead of creating a duplicate", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ success: true, action: "ride_rating" }));

  try {
    const env = mockEnv();

    const firstResponse = await handleRequest(voteRequest(1), env);
    const firstPayload = await firstResponse.json();

    assert.equal(firstResponse.status, 201);
    assert.equal(firstPayload.updated, false);
    assert.deepEqual(firstPayload.summary, {
      ride_slug: "ride-a",
      vote_count: 1,
      average_rating: 1,
    });

    const updateResponse = await handleRequest(voteRequest(5), env);
    const updatePayload = await updateResponse.json();

    assert.equal(updateResponse.status, 200);
    assert.equal(updatePayload.updated, true);
    assert.equal(updatePayload.previous_rating, 1);
    assert.deepEqual(updatePayload.summary, {
      ride_slug: "ride-a",
      vote_count: 1,
      average_rating: 5,
    });
    assert.equal(env.db.votes.length, 1);
    assert.equal(env.db.votes[0].rating, 5);
    assert.deepEqual(env.db.summaries.get("ride-a"), {
      vote_count: 1,
      rating_sum: 5,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("returns the current voter rating with a summary lookup", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ success: true, action: "ride_rating" }));

  try {
    const env = mockEnv();
    await handleRequest(voteRequest(4, "voter-lookup"), env);

    const response = await handleRequest(summaryRequest("voter-lookup"), env);
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.deepEqual(payload, {
      ride_slug: "ride-a",
      vote_count: 1,
      average_rating: 4,
      my_rating: 4,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("returns null current rating when the voter has not rated the ride", async () => {
  const env = mockEnv();
  env.db.summaries.set("ride-a", { vote_count: 2, rating_sum: 7 });

  const response = await handleRequest(summaryRequest("voter-missing"), env);
  const payload = await response.json();

  assert.equal(response.status, 200);
  assert.deepEqual(payload, {
    ride_slug: "ride-a",
    vote_count: 2,
    average_rating: 3.5,
    my_rating: null,
  });
});

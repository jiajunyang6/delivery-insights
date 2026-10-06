/** Report loading: brief API outages are retried, API problems are shown at once. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { UnavailableProblem, loadInsights } from "../src/api.ts";

const params = { repo: "a/b", from: "2026-09-01", to: "2026-09-30" };
const fast = { retryMs: 1, maxRetries: 2 };

/** Replace fetch with replies in order; a string entry rejects like a network failure. */
function replies(...items: (Response | string)[]) {
  let calls = 0;
  globalThis.fetch = async () => {
    const item = items[calls++];
    if (typeof item === "string") throw new TypeError(item);
    return item;
  };
  return () => calls;
}

function json(status: number, body: unknown, type = "application/json") {
  // Retry-After 0 lets the test's minPollMs floor set the poll interval.
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": type, "Retry-After": "0" },
  });
}

test("a restart that drops requests is retried until the report loads", async () => {
  const calls = replies("Failed to fetch", "Failed to fetch", json(200, { snapshot_id: "s" }));
  const insight = await loadInsights(params, new AbortController().signal, () => {}, undefined, fast);
  assert.deepEqual(insight, { snapshot_id: "s" });
  assert.equal(calls(), 3);
});

test("an API problem such as 503 data-unavailable is not retried", async () => {
  const calls = replies(
    json(503, { title: "Data unavailable", detail: "x" }, "application/problem+json"),
  );
  await assert.rejects(
    loadInsights(params, new AbortController().signal, () => {}, undefined, fast),
    (error: Error) => !(error instanceof UnavailableProblem) && error.message === "x",
  );
  assert.equal(calls(), 1);
});

test("an API that stays unreachable ends with a readable error", async () => {
  const calls = replies("Failed to fetch", "Failed to fetch", "Failed to fetch");
  await assert.rejects(
    loadInsights(params, new AbortController().signal, () => {}, undefined, fast),
    (error: Error) =>
      error instanceof UnavailableProblem && /could not reach the API/.test(error.message),
  );
  assert.equal(calls(), 3);
});

function pending(job: { status: string; phase: string } | null) {
  return json(202, {
    status: "pending",
    repos: [{ repo: "a/b", reason: "backfill", covered_since: null, required_since: "x", job }],
  });
}

test("polling continues past the idle limit while a sync job is running", async () => {
  const running = { status: "running", phase: "backfill:120d" };
  const slow = { ...fast, minPollMs: 60, idleMs: 100 };
  const calls = replies(
    pending(running),
    pending(running),
    pending(running),
    json(200, { snapshot_id: "s" }),
  );
  const insight = await loadInsights(params, new AbortController().signal, () => {}, undefined, slow);
  assert.deepEqual(insight, { snapshot_id: "s" });
  assert.equal(calls(), 4);
});

test("polling stops after the idle limit when no sync is active", async () => {
  replies(...Array.from({ length: 10 }, () => pending(null)));
  await assert.rejects(
    loadInsights(params, new AbortController().signal, () => {}, undefined, {
      ...fast,
      minPollMs: 60,
      idleMs: 100,
    }),
    /no sync has been active/,
  );
});

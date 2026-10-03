import assert from "node:assert/strict";
import { test } from "node:test";
import { dateRange } from "../src/format.ts";
import { periodError } from "../src/period.ts";

const limits = {
  earliest_from: "2026-09-03",
  latest_to: "2026-10-03",
  max_days: 366,
};

test("30-day history accepts short presets and rejects the reported 90-day range", () => {
  for (const days of [7, 30])
    assert.equal(periodError(dateRange(days, limits.latest_to), limits), null);
  const range = dateRange(90, limits.latest_to);
  assert.deepEqual(range, { from: "2026-07-06", to: "2026-10-03" });
  assert.match(periodError(range, limits)!, /History is configured from 2026-09-03/);
});

test("90-day preset becomes available when the configured horizon permits it", () => {
  assert.equal(periodError(dateRange(90, limits.latest_to), {
    ...limits, earliest_from: "2026-07-05",
  }), null);
});

test("60-day preset uses exactly 60 UTC dates and respects the configured horizon", () => {
  const range = dateRange(60, limits.latest_to);
  assert.deepEqual(range, { from: "2026-08-05", to: "2026-10-03" });
  assert.match(periodError(range, limits)!, /History is configured from/);
  assert.equal(periodError(range, {
    ...limits, earliest_from: "2026-08-04",
  }), null);
});

test("inclusive horizon boundary is valid; earlier dates and future dates are rejected", () => {
  assert.equal(periodError({ from: limits.earliest_from, to: limits.latest_to }, limits), null);
  assert.match(periodError({ from: "2026-09-02", to: limits.latest_to }, limits)!, /later From/);
  assert.match(periodError({ from: limits.earliest_from, to: "2026-10-04" }, limits)!, /To must/);
});

test("invalid calendar dates, reversed dates, and overlong periods cannot submit", () => {
  for (const from of ["", "2026-02-30", "2026-2-03", "2026-10-04"])
    assert.match(periodError({ from, to: limits.latest_to }, limits)!, /valid date range/);
  assert.match(periodError({ from: "2025-01-01", to: limits.latest_to }, {
    ...limits, earliest_from: "2025-01-01",
  })!, /at most 366 days/);
});

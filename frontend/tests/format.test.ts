import assert from "node:assert/strict";
import { test } from "node:test";
import { format, signed } from "../src/format.ts";

test("review averages remain distinct when one-decimal rounding would hide the change", () => {
  assert.equal(format(0.48, "rounds"), "0.48");
  assert.equal(format(0.53, "rounds"), "0.53");
  assert.equal(signed(-0.0923), "-9.2%");
  assert.equal(format(0, "rounds"), "0");
  assert.equal(format(null, "rounds"), "—");
});

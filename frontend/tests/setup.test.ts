import assert from "node:assert/strict";
import { test } from "node:test";
import { setupItems } from "../src/setup.ts";
import type { SetupStatus } from "../src/types.ts";

const healthy: SetupStatus = {
  github: { token_configured: true, problems: [] },
  llm: {
    key_configured: true,
    region: "us-west-2",
    model_id: "us.anthropic.claude-sonnet-4-6",
    last_error: null,
    last_error_at: null,
  },
};

test("healthy configuration produces no notice", () => {
  assert.deepEqual(setupItems(healthy), []);
});

test("each problem names the .env variable to fix", () => {
  const items = setupItems({
    github: {
      token_configured: true,
      problems: [
        { repo: "a/b", status: "auth_error" },
        { repo: "c/d", status: "not_found" },
      ],
    },
    llm: { ...healthy.llm, last_error: "ValidationException" },
  });
  const text = items.map((i) => i.text).join("\n");
  assert.match(text, /rejected GITHUB_TOKEN while syncing a\/b/);
  assert.match(text, /c\/d was not found\. Check TRACKED_REPOS/);
  assert.match(text, /Check BEDROCK_MODEL_ID and AWS_REGION/);
  assert.ok(items.every((i) => i.level === "problem"));
});

test("missing Bedrock key is informational and auth errors point to the key", () => {
  const off = setupItems({ ...healthy, llm: { ...healthy.llm, key_configured: false } });
  assert.deepEqual(off.map((i) => i.level), ["info"]);
  assert.match(off[0].text, /AWS_BEARER_TOKEN_BEDROCK/);
  const denied = setupItems({
    ...healthy,
    llm: { ...healthy.llm, last_error: "AccessDeniedException" },
  });
  assert.match(denied[0].text, /Check AWS_BEARER_TOKEN_BEDROCK/);
});

test("a stale auth error is hidden while the token is missing", () => {
  const items = setupItems({
    ...healthy,
    github: { token_configured: false, problems: [{ repo: "a/b", status: "auth_error" }] },
  });
  assert.deepEqual(
    items.map((i) => i.text),
    ["Set GITHUB_TOKEN in .env to sync GitHub data."],
  );
});

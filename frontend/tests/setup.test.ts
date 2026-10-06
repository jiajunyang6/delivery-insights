/** Configuration and sync notices: the `.env` variable to fix and failed-sync details. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { setupItems, syncItem } from "../src/setup.ts";
import type { RepoStatus, SetupStatus } from "../src/types.ts";

const healthy: SetupStatus = {
  github: { token_configured: true, problems: [] },
  llm: {
    key_configured: true,
    region: "us-west-2",
    model_id: "us.anthropic.claude-sonnet-4-6",
    last_error: null,
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
        { repo: "a/b", status: "auth_error", syncing: false },
        { repo: "c/d", status: "not_found", syncing: false },
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
    github: { token_configured: false, problems: [{ repo: "a/b", status: "auth_error", syncing: false }] },
  });
  assert.deepEqual(
    items.map((i) => i.text),
    ["Set GITHUB_TOKEN in .env to sync GitHub data."],
  );
});

test("an active sync after a fix shows progress instead of the old failure", () => {
  const items = setupItems({
    ...healthy,
    github: {
      token_configured: true,
      problems: [{ repo: "a/b", status: "missing_token", syncing: true }],
    },
  });
  assert.deepEqual(items.map((i) => i.level), ["info"]);
  assert.match(items[0].text, /Syncing a\/b with the current settings/);
  const idle = setupItems({
    ...healthy,
    github: {
      token_configured: true,
      problems: [{ repo: "a/b", status: "missing_token", syncing: false }],
    },
  });
  assert.match(idle[0].text, /no new sync is queued/);
});

test("a connection failure says the model ID was not checked yet", () => {
  const [item] = setupItems({
    ...healthy,
    llm: { ...healthy.llm, last_error: "EndpointConnectionError" },
  });
  assert.match(item.text, /Could not connect to Bedrock in region us-west-2/);
  assert.match(item.text, /BEDROCK_MODEL_ID was not checked yet/);
});

test("a failed sync shows its error code and when stored data was last current", () => {
  const failed: RepoStatus = {
    repo: "a/b",
    last_sync_status: "failed",
    last_sync_error: "GitHubTransientError: invalid_json",
    last_synced_at: "2026-10-06T00:51:51.929349Z",
    syncing: false,
  };
  const idle = syncItem(failed);
  assert.equal(idle?.level, "problem");
  assert.match(idle!.text, /a\/b failed \(GitHubTransientError: invalid_json\)/);
  assert.match(idle!.text, /current to 2026-10-06 00:51 UTC/);
  const retrying = syncItem({ ...failed, syncing: true });
  assert.equal(retrying?.level, "info");
  assert.match(retrying!.text, /A new sync is running/);
  assert.equal(syncItem({ ...failed, last_sync_status: "ok" }), null);
  assert.equal(syncItem({ ...failed, last_sync_status: "auth_error" }), null);
});

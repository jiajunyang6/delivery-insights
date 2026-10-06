/** Configuration and sync health turned into notices; plain logic so Node tests need no browser. */
import type { RepoStatus, SetupStatus } from "./types";

// Bedrock error codes grouped by the `.env` setting most likely to fix them.
const AUTH_ERRORS = new Set([
  "AccessDeniedException",
  "UnrecognizedClientException",
  "ExpiredTokenException",
  "InvalidSignatureException",
]);
const MODEL_ERRORS = new Set(["ValidationException", "ResourceNotFoundException"]);
const ENDPOINT_ERRORS = new Set(["EndpointConnectionError", "NoRegionError"]);

/** One notice line: a problem the user must fix, or information. */
export type SetupItem = { level: "problem" | "info"; text: string };

/** Turn configuration health into fixes that name the exact `.env` variable to change. */
export function setupItems(setup: SetupStatus): SetupItem[] {
  const items: SetupItem[] = [];
  const { github, llm } = setup;
  if (!github.token_configured)
    items.push({ level: "problem", text: "Set GITHUB_TOKEN in .env to sync GitHub data." });
  for (const p of github.problems) {
    // The status comes from the last finished sync. While a new sync runs it may already use
    // fixed settings, so report progress instead of the old failure.
    if (p.syncing && github.token_configured) {
      items.push({
        level: "info",
        text: `Syncing ${p.repo} with the current settings. This notice clears once the sync succeeds; refresh the page to check.`,
      });
      continue;
    }
    // A stale auth_error is moot until a token is set; the missing-token item covers it.
    if (p.status === "auth_error" && github.token_configured)
      items.push({
        level: "problem",
        text: `GitHub rejected GITHUB_TOKEN while syncing ${p.repo}. Check the token and its Public repositories access.`,
      });
    if (p.status === "not_found")
      items.push({
        level: "problem",
        text: `Repository ${p.repo} was not found. Check TRACKED_REPOS in .env.`,
      });
    if (p.status === "missing_token" && github.token_configured)
      items.push({
        level: "problem",
        text: `${p.repo} last synced without a token and no new sync is queued. Run docker compose up -d so the worker reads GITHUB_TOKEN, then refresh the page.`,
      });
  }
  // A missing Bedrock key is a valid setup (template narratives), so it is only information.
  if (!llm.key_configured) {
    items.push({
      level: "info",
      text: "Narratives use the built-in template. Set AWS_BEARER_TOKEN_BEDROCK in .env to enable LLM narratives.",
    });
  } else if (llm.last_error) {
    const code = llm.last_error;
    const where = `model ${llm.model_id} in region ${llm.region}`;
    const text = AUTH_ERRORS.has(code)
      ? `Bedrock rejected the request (${code}). Check AWS_BEARER_TOKEN_BEDROCK and model access for ${where}.`
      : MODEL_ERRORS.has(code)
        ? `Bedrock did not accept ${where} (${code}). Check BEDROCK_MODEL_ID and AWS_REGION.`
        : ENDPOINT_ERRORS.has(code)
          ? `Could not connect to Bedrock in region ${llm.region} (${code}), so BEDROCK_MODEL_ID was not checked yet. Check AWS_REGION and network or proxy access from the containers.`
          : `The last Bedrock request failed (${code}); narratives fell back to the template. Check AWS_BEARER_TOKEN_BEDROCK, BEDROCK_MODEL_ID and AWS_REGION.`;
    items.push({ level: "problem", text });
  }
  return items;
}

/**
 * Describe a failed last sync of one repository, or return null when it did not fail.
 * Auth, not-found and missing-token statuses are left to the Configuration notice.
 */
export function syncItem(repo: RepoStatus | undefined): SetupItem | null {
  if (!repo || repo.last_sync_status !== "failed") return null;
  const failure = `The last sync of ${repo.repo} failed (${repo.last_sync_error ?? "unknown error"}).`;
  if (repo.syncing)
    return {
      level: "info",
      text: `${failure} A new sync is running; this notice clears once it succeeds.`,
    };
  const stored = repo.last_synced_at
    ? `Stored data is current to ${new Date(repo.last_synced_at).toISOString().slice(0, 16).replace("T", " ")} UTC.`
    : "No data has been stored yet.";
  return {
    level: "problem",
    text: `${failure} ${stored} The worker retries on its next scheduled sync; if the failure repeats, check docker compose logs worker.`,
  };
}

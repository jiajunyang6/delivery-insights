import type { SetupStatus } from "./types";

// Bedrock error codes grouped by the `.env` setting most likely to fix them.
const AUTH_ERRORS = new Set([
  "AccessDeniedException",
  "UnrecognizedClientException",
  "ExpiredTokenException",
  "InvalidSignatureException",
]);
const MODEL_ERRORS = new Set(["ValidationException", "ResourceNotFoundException"]);
const ENDPOINT_ERRORS = new Set(["EndpointConnectionError", "NoRegionError"]);

export type SetupItem = { level: "problem" | "info"; text: string };

/** Turn configuration health into fixes that name the exact `.env` variable to change. */
export function setupItems(setup: SetupStatus): SetupItem[] {
  const items: SetupItem[] = [];
  const { github, llm } = setup;
  if (!github.token_configured)
    items.push({ level: "problem", text: "Set GITHUB_TOKEN in .env to sync GitHub data." });
  for (const p of github.problems) {
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
        text: `${p.repo} last synced without a token. Restart the services after setting GITHUB_TOKEN.`,
      });
  }
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
          ? `Bedrock could not be reached in region ${llm.region} (${code}). Check AWS_REGION and network access.`
          : `The last Bedrock request failed (${code}); narratives fell back to the template.`;
    items.push({ level: "problem", text });
  }
  return items;
}

/** API access for the dashboard: JSON fetching, problem errors, polling and cancellation. */
import { useEffect, type DependencyList } from "react";
import type { Insight, Params, Pending } from "./types";

/** Abort superseded requests and the current request on unmount. */
export function useAbortable(
  effect: (signal: AbortSignal) => void,
  dependencies: DependencyList,
) {
  useEffect(() => {
    const controller = new AbortController();
    effect(controller.signal);
    return () => controller.abort();
  }, dependencies);
}

// Same-origin by default: nginx (and the Vite dev server) proxy /api to FastAPI. Node tests
// have no import.meta.env, hence the optional chaining.
const API_BASE = import.meta.env?.VITE_API_BASE_URL ?? "/api";

/** An RFC 9457 problem from the API, or a client-side failure shaped like one. */
export class ApiProblem extends Error {
  title: string;
  detail: string;
  status: number;
  request_id?: string;
  errors: { param: string; message: string }[];

  /** Plain fields instead of parameter properties, so Node can strip the types in tests. */
  constructor(
    title: string,
    detail: string,
    status: number,
    request_id?: string,
    errors: { param: string; message: string }[] = [],
  ) {
    super(detail);
    this.title = title;
    this.detail = detail;
    this.status = status;
    this.request_id = request_id;
    this.errors = errors;
  }
}
/** No usable reply: the API or its proxy is down or restarting, so a retry may succeed. */
export class UnavailableProblem extends ApiProblem {}

/** Normalize any thrown value to an ApiProblem the UI can display. */
export function message(error: unknown): ApiProblem {
  return error instanceof ApiProblem
    ? error
    : new ApiProblem(
        "Unable to load data",
        error instanceof Error ? error.message : "Please try again.",
        0,
      );
}
/**
 * GET a JSON resource from the API. Throws ApiProblem for problem+json, non-2xx or non-JSON
 * replies; returns the status and headers too, because 202 Pending is a success status.
 */
export async function fetchJson<T>(
  path: string,
  signal: AbortSignal,
  cache?: RequestCache,
) {
  let response: Response;
  try {
    response = await fetch(API_BASE + path, {
      signal,
      cache,
      headers: { Accept: "application/json" },
    });
  } catch (error) {
    if (signal.aborted) throw error;
    // fetch rejects only when no HTTP reply arrived, typically while the containers restart.
    throw new UnavailableProblem(
      "Cannot reach the service",
      "The dashboard could not reach the API. If the containers are restarting, wait a moment and refresh.",
      0,
    );
  }
  const contentType = response.headers
    .get("Content-Type")
    ?.split(";")[0]
    ?.trim()
    .toLowerCase();
  // Non-JSON replies (e.g. a proxy's HTML error page) become a readable problem instead of a
  // JSON parse error.
  if (contentType !== "application/json" && !contentType?.endsWith("+json")) {
    throw new UnavailableProblem(
      "Service temporarily unavailable",
      `The service is temporarily unavailable (HTTP ${response.status}). Try again shortly.`,
      response.status,
      response.headers.get("X-Request-ID") ?? undefined,
    );
  }
  const data = await response.json();
  if (contentType === "application/problem+json" || !response.ok) {
    throw new ApiProblem(
      data.title ?? "Request failed",
      data.detail ?? "Please try again.",
      response.status,
      data.request_id ?? response.headers.get("X-Request-ID") ?? undefined,
      data.errors ?? [],
    );
  }
  return {
    status: response.status,
    data: data as T,
    headers: response.headers,
  };
}
/** Encode report parameters as a query string. */
function query(params: Params): string {
  return new URLSearchParams(params).toString();
}

/** Sleep for the given time; reject with AbortError as soon as the signal aborts. */
function wait(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const stop = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", stop);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", stop, { once: true });
    if (signal.aborted) stop();
  });
}
/** How often and how many times in a row an unreachable API is retried. */
export type RetryOptions = { retryMs?: number; maxRetries?: number };

/**
 * Run `request`, retrying an unreachable API every `retryMs` up to `maxRetries` times
 * (about 30 s by default), which covers a container restart.
 */
export async function retryUnavailable<T>(
  request: () => Promise<T>,
  signal: AbortSignal,
  { retryMs = 5_000, maxRetries = 6 }: RetryOptions = {},
): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await request();
    } catch (error) {
      // Only a missing or non-JSON reply is retried; API problems such as 503
      // data-unavailable are answers, not outages, and show at once.
      if (!(error instanceof UnavailableProblem) || attempt >= maxRetries) throw error;
      await wait(retryMs, signal);
    }
  }
}
/** Polling timing: outage retries, the Retry-After floor and the wait without an active sync. */
export type PollOptions = RetryOptions & { minPollMs?: number; idleMs?: number };

/**
 * Poll while the API answers 202 Pending (data still syncing), honoring Retry-After with a
 * `minPollMs` floor. Polling continues while a sync job is queued or running, however long
 * a backfill takes; with no active job nothing changes until the next scheduled sync, so it
 * stops after `idleMs` (five minutes). Each request rides out a brief outage.
 */
export async function loadInsights(
  params: Params,
  signal: AbortSignal,
  onPending: (p: Pending) => void,
  cache?: RequestCache,
  { minPollMs = 5_000, idleMs = 300_000, ...retry }: PollOptions = {},
) {
  let deadline = Date.now() + idleMs;
  while (!signal.aborted) {
    const result = await retryUnavailable(
      () =>
        fetchJson<Insight | Pending>(
          "/v1/insights/delivery?" + query(params),
          signal,
          cache,
        ),
      signal,
      retry,
    );
    if (result.status === 200) return result.data as Insight;
    if (result.status !== 202)
      throw new ApiProblem(
        "Unexpected response",
        "Please refresh.",
        result.status,
      );
    const pending = result.data as Pending;
    onPending(pending);
    // A queued or running job is making progress even when coverage has not moved yet, so
    // it keeps the page waiting.
    if (pending.repos.some((repo) => repo.job !== null)) deadline = Date.now() + idleMs;
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    const raw = Number(result.headers.get("Retry-After") ?? 30);
    const delay = Number.isFinite(raw) ? Math.max(minPollMs, raw * 1000) : 30_000;
    await wait(Math.min(remaining, delay), signal);
    if (Date.now() >= deadline) break;
  }
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  throw new ApiProblem(
    "No sync is running",
    "The data for this period is not ready and no sync has been active for five minutes. " +
      "The worker starts one on its schedule; refresh later, and check the Sync notice if " +
      "the last sync failed.",
    202,
  );
}
/** Describe why one repository's data is not ready yet, in user-facing words. */
export function pendingText(repo: Pending["repos"][number]): string {
  const text = {
    never_synced: "Not synced yet",
    backfill:
      "Backfilling history: covered since " +
      (repo.covered_since ?? "unknown") +
      ", needs " +
      repo.required_since,
    open_sweep: "Scanning open pull requests",
    rederive:
      "Recomputing derived data after a configuration or version change",
    stale: "Sync has not reached the requested period yet",
  };
  return text[repo.reason];
}

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

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";
export class ApiProblem extends Error {
  constructor(
    public title: string,
    public detail: string,
    public status: number,
    public request_id?: string,
    public errors: { param: string; message: string }[] = [],
  ) {
    super(detail);
  }
}
export function message(error: unknown): ApiProblem {
  return error instanceof ApiProblem
    ? error
    : new ApiProblem(
        "Unable to load data",
        error instanceof Error ? error.message : "Please try again.",
        0,
      );
}
export async function fetchJson<T>(
  path: string,
  signal: AbortSignal,
  cache?: RequestCache,
) {
  const response = await fetch(API_BASE + path, {
    signal,
    cache,
    headers: { Accept: "application/json" },
  });
  const contentType = response.headers
    .get("Content-Type")
    ?.split(";")[0]
    ?.trim()
    .toLowerCase();
  // Non-JSON replies (e.g. a proxy's HTML error page) become a readable problem instead of a
  // JSON parse error.
  if (contentType !== "application/json" && !contentType?.endsWith("+json")) {
    throw new ApiProblem(
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
function query(params: Params): string {
  return new URLSearchParams(params).toString();
}
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
/**
 * Poll while the API answers 202 Pending (data still syncing), honoring Retry-After with a
 * 5 s floor, and give up after five minutes so the page never polls indefinitely.
 */
export async function loadInsights(
  params: Params,
  signal: AbortSignal,
  onPending: (p: Pending) => void,
  cache?: RequestCache,
) {
  const deadline = Date.now() + 300_000;
  while (!signal.aborted) {
    const result = await fetchJson<Insight | Pending>(
      "/v1/insights/delivery?" + query(params),
      signal,
      cache,
    );
    if (result.status === 200) return result.data as Insight;
    if (result.status !== 202)
      throw new ApiProblem(
        "Unexpected response",
        "Please refresh.",
        result.status,
      );
    onPending(result.data as Pending);
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    const raw = Number(result.headers.get("Retry-After") ?? 30);
    const seconds = Number.isFinite(raw) ? Math.max(5, raw) : 30;
    await wait(Math.min(remaining, seconds * 1000), signal);
    if (Date.now() >= deadline) break;
  }
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  throw new ApiProblem(
    "Sync is still in progress",
    "Automatic retries stopped after five minutes. Refresh later.",
    202,
  );
}
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

import type { Params, Pending, Snapshot } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";
export class ApiProblem extends Error {
  constructor(
    public title: string,
    public detail: string,
    public status: number,
    public request_id?: string,
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
export async function fetchJson<T>(path: string, signal: AbortSignal) {
  const response = await fetch(API_BASE + path, {
    signal,
    headers: { Accept: "application/json" },
  });
  const data = await response.json();
  if (
    response.headers
      .get("Content-Type")
      ?.includes("application/problem+json") ||
    !response.ok
  ) {
    throw new ApiProblem(
      data.title ?? "Request failed",
      data.detail ?? "Please try again.",
      response.status,
      data.request_id ?? response.headers.get("X-Request-ID") ?? undefined,
    );
  }
  return {
    status: response.status,
    data: data as T,
    headers: response.headers,
  };
}
export function query(params: Params): string {
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
export async function loadInsights(
  params: Params,
  signal: AbortSignal,
  onPending: (p: Pending) => void,
) {
  const deadline = Date.now() + 300_000;
  while (!signal.aborted) {
    const result = await fetchJson<Snapshot | Pending>(
      "/v1/insights/delivery?" + query(params),
      signal,
    );
    if (result.status === 200) return result.data as Snapshot;
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

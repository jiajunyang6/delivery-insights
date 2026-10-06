/** Dashboard shell: report settings, sync progress, errors and the report itself. */
import { useEffect, useState } from "react";
import {
  ApiProblem,
  fetchJson,
  loadInsights,
  message,
  pendingText,
  retryUnavailable,
  useAbortable,
} from "./api";
import { dateRange, defaultRange, periodError } from "./format";
import type {
  DateLimits,
  Params,
  Pending,
  RepoStatus,
  RepoList,
  SetupStatus,
  Insight,
} from "./types";
import { Controls } from "./components/Controls";
import { TimeLedgerChart } from "./components/TimeLedgerChart";
import { NarrativePanel } from "./components/NarrativePanel";
import { SetupNotice } from "./components/SetupNotice";
import { SyncNotice } from "./components/SyncNotice";

// The URL carries repo/from/to, so a report link can be shared or reloaded.
const initial = new URLSearchParams(window.location.search);
const periodFromUrl = initial.has("from") || initial.has("to");
const defaults = dateRange(60);

/**
 * Load tracked repositories and date limits, then the insight for the selected period. While
 * the API answers 202 the page shows sync progress and keeps polling.
 */
export default function App() {
  const [params, setParams] = useState<Params>({
    repo: initial.get("repo") ?? "",
    from: initial.get("from") ?? defaults.from,
    to: initial.get("to") ?? defaults.to,
  });
  const [repos, setRepos] = useState<RepoStatus[]>([]);
  const [dateLimits, setDateLimits] = useState<DateLimits | null>(null);
  const [insight, setInsight] = useState<Insight | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<ApiProblem | null>(null);
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [setup, setSetup] = useState<SetupStatus | null>(null);
  const [setupCheck, setSetupCheck] = useState(0);
  const validationError = periodError(params, dateLimits);
  const invalid = validationError !== null;
  // Repositories, date limits and setup health; reloaded by "Refresh report". An unknown
  // repo from the URL falls back to the first tracked one. On the first load without dates in
  // the URL, the period becomes the longest preset the server's history supports, anchored to
  // its UTC today; later reloads keep the user's choice.
  useAbortable((signal) => {
    retryUnavailable(() => fetchJson<RepoList>("/v1/repos", signal), signal)
      .then((r) => {
        if (signal.aborted) return;
        setRepos(r.data.items);
        setDateLimits(r.data.date_limits);
        setSetup(r.data.setup);
        setParams((p) => ({
          ...p,
          ...(refresh === 0 && !periodFromUrl ? defaultRange(r.data.date_limits) : {}),
          repo: r.data.items.some((repo) => repo.repo === p.repo)
            ? p.repo
            : (r.data.items[0]?.repo ?? ""),
        }));
        if (!r.data.items.length)
          setError(
            new ApiProblem(
              "No tracked repositories",
              "Configure TRACKED_REPOS and restart the services.",
              0,
            ),
          );
      })
      .catch((e) => {
        if (!signal.aborted) setError(message(e));
      });
  }, [refresh]);
  // Re-read setup and sync statuses (not the report) on each pending poll, after a report
  // loads or fails, or when a narrative reports a Bedrock failure, so labels stay current.
  useAbortable(
    (signal) => {
      if (!setupCheck) return;
      fetchJson<RepoList>("/v1/repos", signal)
        .then((r) => {
          if (signal.aborted) return;
          setSetup(r.data.setup);
          setRepos(r.data.items);
        })
        .catch(() => undefined);
    },
    [setupCheck],
  );
  useEffect(() => {
    window.history.replaceState(null, "", "?" + new URLSearchParams(params));
  }, [params]);
  // The report: waits for a repo, valid dates and the date limits, then polls through 202s.
  // A manual refresh bypasses the browser cache so a just-finished sync shows at once.
  useAbortable((signal) => {
    setInsight(null);
    setPending(null);
    if (!params.repo || invalid || !dateLimits) {
      if (params.repo && invalid) setError(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    loadInsights(
      params,
      signal,
      (p) => {
        if (signal.aborted) return;
        setPending(p);
        // Keep the repository labels and Sync notice current during a long sync.
        setSetupCheck((n) => n + 1);
      },
      refresh > 0 ? "no-cache" : undefined,
    )
      .then((s) => {
        if (!signal.aborted) {
          setInsight(s);
          setPending(null);
          // A finished sync may have resolved configuration problems shown earlier.
          setSetupCheck((n) => n + 1);
        }
      })
      .catch((e) => {
        if (!signal.aborted) {
          setPending(null);
          setError(message(e));
          // Show the sync status behind the error, such as a failed last sync.
          setSetupCheck((n) => n + 1);
        }
      })
      .finally(() => {
        if (!signal.aborted) setLoading(false);
      });
  }, [params.repo, params.from, params.to, refresh, invalid, dateLimits]);
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to report
      </a>
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            ↗
          </span>
          <div>
            Delivery Insights<small>ENGINEERING INTELLIGENCE</small>
          </div>
        </div>
        <span className="header-note">A clearer view of how work moves.</span>
      </header>
      <main id="main">
        <Controls
          params={params}
          setParams={setParams}
          repos={repos}
          dateLimits={dateLimits}
          validationError={validationError}
          refresh={() => setRefresh((r) => r + 1)}
        />
        <SetupNotice setup={setup} />
        <SyncNotice repo={repos.find((r) => r.repo === params.repo)} />
        {error && (
          <div className="error-banner" role="alert">
            <strong>{error.title}</strong>
            <p>{error.detail}</p>
            {error.errors.length > 0 && (
              <ul>
                {error.errors.map((item, index) => (
                  <li key={index}>{item.param}: {item.message}</li>
                ))}
              </ul>
            )}
            {error.request_id && <small>Request {error.request_id}</small>}
          </div>
        )}
        {pending && (
          <section className="panel pending" role="status">
            <h2>
              <span className="spinner" />
              Preparing your report
            </h2>
            {pending.repos.map((r) => (
              <div key={r.repo}>
                <strong>{r.repo}</strong>
                <p>{pendingText(r)}</p>
                {r.job && (
                  <small>
                    {r.job.status} · {r.job.phase ?? "Queued"}
                  </small>
                )}
              </div>
            ))}
            <p className="footnote">
              This page keeps checking while a sync is running; a full backfill can take
              several minutes.
            </p>
          </section>
        )}
        {!insight && !pending && !error && (
          <div className="panel loading" role="status">
            <span className="spinner" />
            {loading
              ? "Computing delivery insights…"
              : invalid
                ? "Select a valid period to begin."
                : "Connecting to repositories…"}
          </div>
        )}
        {insight && (
          <div className="report">
            <NarrativePanel
              insight={insight}
              onLlmError={() => setSetupCheck((n) => n + 1)}
            />
            <TimeLedgerChart insight={insight} />
            <footer className="report-footer">
              <span>Delivery Insights · Evidence before conclusions.</span>
              <code>{insight.snapshot_id}</code>
            </footer>
          </div>
        )}
      </main>
    </>
  );
}

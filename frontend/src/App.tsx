import { useEffect, useState } from "react";
import { useAbortable } from "./hooks/useAbortable";
import {
  ApiProblem,
  fetchJson,
  loadInsights,
  message,
  pendingText,
} from "./api";
import { dateRange } from "./format";
import { periodError } from "./period";
import type {
  DateLimits,
  Params,
  Pending,
  RepoStatus,
  RepoList,
  SetupStatus,
  Snapshot,
} from "./types";
import { Controls } from "./components/Controls";
import { TimeLedgerChart } from "./components/TimeLedgerChart";
import { NarrativePanel } from "./components/NarrativePanel";
import { SetupNotice } from "./components/SetupNotice";

const initial = new URLSearchParams(window.location.search);
const defaults = dateRange(30);
export default function App() {
  const [params, setParams] = useState<Params>({
    repo: initial.get("repo") ?? "",
    from: initial.get("from") ?? defaults.from,
    to: initial.get("to") ?? defaults.to,
  });
  const [repos, setRepos] = useState<RepoStatus[]>([]);
  const [dateLimits, setDateLimits] = useState<DateLimits | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<ApiProblem | null>(null);
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [setup, setSetup] = useState<SetupStatus | null>(null);
  const [setupCheck, setSetupCheck] = useState(0);
  const validationError = periodError(params, dateLimits);
  const invalid = validationError !== null;
  useAbortable((signal) => {
    fetchJson<RepoList>("/v1/repos", signal)
      .then((r) => {
        if (signal.aborted) return;
        setRepos(r.data.items);
        setDateLimits(r.data.date_limits);
        setSetup(r.data.setup);
        setParams((p) => ({
          ...p,
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
  // Re-read setup and sync statuses (not the report) after a report loads or a narrative
  // reports a Bedrock failure, so a sync that has since succeeded clears stale labels.
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
  useAbortable((signal) => {
    setSnapshot(null);
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
        if (!signal.aborted) setPending(p);
      },
      refresh > 0 ? "no-cache" : undefined,
    )
      .then((s) => {
        if (!signal.aborted) {
          setSnapshot(s);
          setPending(null);
          // A finished sync may have resolved configuration problems shown earlier.
          setSetupCheck((n) => n + 1);
        }
      })
      .catch((e) => {
        if (!signal.aborted) {
          setPending(null);
          setError(message(e));
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
              This page retries automatically for up to five minutes.
            </p>
          </section>
        )}
        {!snapshot && !pending && !error && (
          <div className="panel loading" role="status">
            <span className="spinner" />
            {loading
              ? "Computing delivery insights…"
              : invalid
                ? "Select a valid period to begin."
                : "Connecting to repositories…"}
          </div>
        )}
        {snapshot && (
          <div className="report">
            <NarrativePanel
              snapshot={snapshot}
              onLlmError={() => setSetupCheck((n) => n + 1)}
            />
            <TimeLedgerChart snapshot={snapshot} />
            <footer className="report-footer">
              <span>Delivery Insights · Evidence before conclusions.</span>
              <code>{snapshot.snapshot_id}</code>
            </footer>
          </div>
        )}
      </main>
    </>
  );
}

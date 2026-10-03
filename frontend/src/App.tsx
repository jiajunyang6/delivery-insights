import { useEffect, useState } from "react";
import {
  ApiProblem,
  fetchJson,
  loadInsights,
  message,
  pendingText,
} from "./api";
import { dateRange } from "./format";
import type {
  Audience,
  Lang,
  Params,
  Pending,
  RepoStatus,
  Snapshot,
} from "./types";
import { Controls } from "./components/Controls";
import { Headline } from "./components/Headline";
import { KpiGrid } from "./components/KpiGrid";
import { TimeLedgerChart } from "./components/TimeLedgerChart";
import { Bottlenecks } from "./components/Bottlenecks";
import { ReviewQueueChart } from "./components/ReviewQueueChart";
import { LocationsTable } from "./components/LocationsTable";
import { AtRiskTable } from "./components/AtRiskTable";
import { NarrativePanel } from "./components/NarrativePanel";

const initial = new URLSearchParams(window.location.search);
const defaults = dateRange(30);
export default function App() {
  const [params, setParams] = useState<Params>({
    repo: initial.get("repo") ?? "",
    from: initial.get("from") ?? defaults.from,
    to: initial.get("to") ?? defaults.to,
  });
  const [audience, setAudience] = useState<Audience>(
    initial.get("audience") === "director" ? "director" : "manager",
  );
  const [lang, setLang] = useState<Lang>(
    initial.get("lang") === "zh" ? "zh" : "en",
  );
  const [repos, setRepos] = useState<RepoStatus[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<ApiProblem | null>(null);
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const validDate = (value: string) =>
    /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value));
  const invalid =
    !validDate(params.from) || !validDate(params.to) || params.from > params.to;
  useEffect(() => {
    const controller = new AbortController();
    fetchJson<{ items: RepoStatus[] }>("/v1/repos", controller.signal)
      .then((r) => {
        if (controller.signal.aborted) return;
        setRepos(r.data.items);
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
        if (!controller.signal.aborted) setError(message(e));
      });
    return () => controller.abort();
  }, [refresh]);
  useEffect(() => {
    const query = new URLSearchParams({ ...params, audience, lang });
    window.history.replaceState(null, "", "?" + query);
  }, [params, audience, lang]);
  useEffect(() => {
    const controller = new AbortController();
    setSnapshot(null);
    setPending(null);
    if (!params.repo || invalid) {
      setLoading(false);
      return () => controller.abort();
    }
    setLoading(true);
    setError(null);
    loadInsights(params, controller.signal, (p) => {
      if (!controller.signal.aborted) setPending(p);
    })
      .then((s) => {
        if (!controller.signal.aborted) {
          setSnapshot(s);
          setPending(null);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setPending(null);
          setError(message(e));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [params.repo, params.from, params.to, refresh, invalid]);
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
          audience={audience}
          setAudience={setAudience}
          lang={lang}
          setLang={setLang}
          invalid={invalid}
          refresh={() => setRefresh((r) => r + 1)}
        />
        {error && (
          <div className="error-banner" role="alert">
            <strong>{error.title}</strong>
            <p>{error.detail}</p>
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
            <Headline snapshot={snapshot} />
            <KpiGrid snapshot={snapshot} />
            <TimeLedgerChart snapshot={snapshot} />
            <Bottlenecks snapshot={snapshot} audience={audience} />
            {audience === "manager" && (
              <>
                <ReviewQueueChart
                  weeks={snapshot.bottleneck_analysis.review_queue.weeks}
                />
                <LocationsTable
                  locations={snapshot.bottleneck_analysis.locations}
                />
                <AtRiskTable
                  key={snapshot.snapshot_id}
                  snapshot={snapshot}
                  params={params}
                />
              </>
            )}
            <NarrativePanel
              snapshotId={snapshot.snapshot_id}
              audience={audience}
              lang={lang}
            />
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

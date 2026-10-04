import { useState } from "react";
import { useAbortable } from "../hooks/useAbortable";
import { GithubLink } from "./GithubLink";
import { fetchJson, message, query } from "../api";
import { hours, stateLabels } from "../format";
import type { Params, PrPage, RiskPr, Snapshot } from "../types";
export function AtRiskTable({
  snapshot: s,
  params,
  onRefresh,
}: {
  snapshot: Snapshot;
  params: Params;
  onRefresh: () => void;
}) {
  const [extra, setExtra] = useState<RiskPr[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [total, setTotal] = useState(s.at_risk_summary.total);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dataChanged, setDataChanged] = useState(false);
  const startRequest = useAbortable();
  function invalidate() {
    setExtra([]);
    setCursor(null);
    setDataChanged(true);
    setError(
      "Data changed since this report. Refresh the report to load matching PRs.",
    );
  }
  async function load() {
    const signal = startRequest();
    setBusy(true);
    setError("");
    try {
      const path =
        "/v1/insights/delivery/prs?" +
        query(params) +
        "&at_risk=true&limit=50" +
        (cursor ? "&cursor=" + encodeURIComponent(cursor) : "");
      const response = await fetchJson<PrPage>(path, signal, "no-cache");
      if (signal.aborted) return;
      if (response.status !== 200)
        throw new Error(
          "Data is syncing. Refresh the report before loading more.",
        );
      // Rows from a newer snapshot would not match the report shown, so stop paging.
      if (response.data.snapshot_id !== s.snapshot_id) {
        invalidate();
        return;
      }
      const items: RiskPr[] = response.data.items.flatMap((p) =>
        p.at_risk && p.current_state && p.current_state_age_hours != null
          ? [
              {
                repo: p.repo,
                number: p.number,
                title: p.title,
                url: p.url,
                author: p.author,
                state: p.current_state,
                age_hours: p.current_state_age_hours,
                threshold_hours: p.at_risk.threshold_hours,
                severity: p.at_risk.severity,
              },
            ]
          : [],
      );
      setExtra((previous) =>
        cursor ? [...(previous ?? []), ...items] : items,
      );
      setCursor(response.data.next_cursor);
      setTotal(response.data.total);
    } catch (e) {
      if (!signal.aborted) {
        const problem = message(e);
        // Cursors are bound to a snapshot; the API rejects them once the data changes.
        if (
          problem.status === 422 &&
          problem.errors.some((p) => p.param === "cursor")
        ) {
          invalidate();
        } else {
          setError(
            problem.status === 422
              ? (problem.errors[0]?.message ?? problem.detail)
              : problem.detail,
          );
        }
      }
    } finally {
      if (!signal.aborted) setBusy(false);
    }
  }
  const rows = extra ?? s.at_risk_prs.slice(0, 5);
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>
            PRs that need attention <span className="count">{total}</span>
          </h2>
          <p>
            Opened or active this period and waiting beyond their repository's
            historical baseline. Extend the date range to include older PRs.
          </p>
        </div>
        <span>{s.at_risk_summary.critical} critical</span>
      </div>
      <p className="footnote">
        Age is time spent in the current waiting state; threshold is the
        repository's historical limit for that state. Critical means the PR also
        exceeds the higher critical threshold. These are waiting signals, not
        individual performance scores.
      </p>
      {dataChanged ? null : !rows.length ? (
        <p className="empty">
          No PRs opened or active this period exceed the waiting-time threshold.
        </p>
      ) : (
        <div className="table-scroll">
          <table className="risk-table">
            <thead>
              <tr>
                <th>Pull request</th>
                <th>Author</th>
                <th>Waiting on</th>
                <th>Age / threshold</th>
                <th>Severity</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.repo + "/" + p.number}>
                  <td>
                    <GithubLink url={p.url}>#{p.number} {p.title}</GithubLink>
                  </td>
                  <td>{p.author ?? "deleted user"}</td>
                  <td>{stateLabels[p.state]}</td>
                  <td>
                    {hours(p.age_hours)}{" "}
                    <span className="muted">/ {hours(p.threshold_hours)}</span>
                  </td>
                  <td>
                    <span className={"badge " + p.severity}>{p.severity}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {dataChanged && <button onClick={onRefresh}>Refresh report</button>}
      {!dataChanged && total > 0 && (
        <div className="table-footer">
          <span>
            Showing {rows.length} of {total}
          </span>
          {rows.length < total && (extra === null || cursor !== null) && (
            <button onClick={load} disabled={busy}>
              {busy ? "Loading…" : "Load more"}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

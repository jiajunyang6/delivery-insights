import type { Audience, Params, RepoStatus } from "../types";
import { dateRange } from "../format";

interface Props {
  params: Params;
  setParams: (p: Params) => void;
  repos: RepoStatus[];
  audience: Audience;
  setAudience: (a: Audience) => void;
  invalid: boolean;
  refresh: () => void;
}
export function Controls(p: Props) {
  return (
    <section className="controls" aria-label="Report settings">
      <div className="control-row">
        <label className="repo-control">
          Repository
          <select
            value={p.params.repo}
            onChange={(e) => p.setParams({ ...p.params, repo: e.target.value })}
          >
            {!p.repos.length && <option value="">Loading repositories…</option>}
            {p.repos.map((r) => (
              <option key={r.repo} value={r.repo}>
                {r.repo}
                {r.last_sync_status !== "ok" ? " · " + r.last_sync_status : ""}
              </option>
            ))}
          </select>
        </label>
        <div className="range-control">
          <span className="label">Period · UTC</span>
          <div className="segmented">
            {[7, 30, 90].map((days) => {
              const range = dateRange(days);
              return (
                <button
                  type="button"
                  key={days}
                  className={
                    range.from === p.params.from && range.to === p.params.to
                      ? "selected"
                      : ""
                  }
                  onClick={() => p.setParams({ ...p.params, ...range })}
                >
                  Last {days} days
                </button>
              );
            })}
          </div>
        </div>
        <label>
          From
          <input
            type="date"
            value={p.params.from}
            max={p.params.to}
            onChange={(e) => p.setParams({ ...p.params, from: e.target.value })}
          />
        </label>
        <label>
          To
          <input
            type="date"
            value={p.params.to}
            max={dateRange(1).to}
            onChange={(e) => p.setParams({ ...p.params, to: e.target.value })}
          />
        </label>
        <button
          className="primary refresh"
          disabled={p.invalid || !p.params.repo}
          onClick={p.refresh}
        >
          Refresh report ↗
        </button>
      </div>
      <div className="control-row secondary-controls">
        <p>PRs opened or with human activity during the selected period.</p>
        <div className="control-row compact">
          <span className="label">View</span>
          <div className="segmented">
            {(["director", "manager"] as const).map((a) => (
              <button
                key={a}
                className={p.audience === a ? "selected" : ""}
                aria-pressed={p.audience === a}
                onClick={() => p.setAudience(a)}
              >
                {a === "director" ? "Director" : "Manager"}
              </button>
            ))}
          </div>
        </div>
      </div>
      {p.invalid && (
        <p className="error-text">
          Choose a valid date range with From no later than To.
        </p>
      )}
    </section>
  );
}

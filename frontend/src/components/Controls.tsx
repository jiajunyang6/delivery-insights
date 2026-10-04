import type { Audience, DateLimits, Params, RepoStatus } from "../types";
import { dateRange } from "../format";
import { isPeriodSelected, periodError } from "../period";
import { viewDescriptions, viewLabels } from "../views";

interface Props {
  params: Params;
  setParams: (p: Params) => void;
  repos: RepoStatus[];
  audience: Audience;
  setAudience: (a: Audience) => void;
  dateLimits: DateLimits | null;
  validationError: string | null;
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
            {[7, 30, 60].map((days) => {
              const range = dateRange(days, p.dateLimits?.latest_to);
              const selected = isPeriodSelected(p.params, days);
              const unavailable = p.dateLimits
                ? periodError(range, p.dateLimits)
                : "Loading supported dates…";
              return (
                <button
                  type="button"
                  key={days}
                  disabled={unavailable !== null}
                  title={unavailable ?? undefined}
                  aria-pressed={selected}
                  className={selected ? "selected" : ""}
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
            min={p.dateLimits?.earliest_from}
            max={p.params.to}
            onChange={(e) => p.setParams({ ...p.params, from: e.target.value })}
          />
        </label>
        <label>
          To
          <input
            type="date"
            value={p.params.to}
            min={p.dateLimits?.earliest_from}
            max={p.dateLimits?.latest_to ?? dateRange(1).to}
            onChange={(e) => p.setParams({ ...p.params, to: e.target.value })}
          />
        </label>
        <button
          className="primary refresh"
          disabled={p.validationError !== null || !p.params.repo || !p.dateLimits}
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
                title={viewDescriptions[a]}
                onClick={() => p.setAudience(a)}
              >
                {viewLabels[a]}
              </button>
            ))}
          </div>
        </div>
      </div>
      {p.dateLimits && (
        <p className="footnote">
          Supported dates: {p.dateLimits.earliest_from} – {p.dateLimits.latest_to} (UTC).
          Longer presets are unavailable when history is configured for a shorter period.
        </p>
      )}
      {p.validationError && (
        <p className="error-text" role="alert">{p.validationError}</p>
      )}
    </section>
  );
}

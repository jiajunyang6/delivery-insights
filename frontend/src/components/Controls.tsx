/** Report settings: repository, period presets, custom UTC dates and refresh. */
import type { DateLimits, Params, RepoStatus } from "../types";
import { PRESET_DAYS, dateRange, isPeriodSelected, periodError } from "../format";

/** Current parameters and the callbacks and limits the controls need. */
interface Props {
  params: Params;
  setParams: (p: Params) => void;
  repos: RepoStatus[];
  dateLimits: DateLimits | null;
  validationError: string | null;
  refresh: () => void;
}
/**
 * Render the settings row. Presets outside the configured history are disabled with the
 * reason as a tooltip; refresh stays disabled until the period is valid.
 */
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
                {r.syncing
                  ? " · syncing"
                  : r.last_sync_status !== "ok"
                    ? " · " + r.last_sync_status
                    : ""}
              </option>
            ))}
          </select>
        </label>
        <div className="range-control">
          <span className="label">Period · UTC</span>
          <div className="segmented">
            {PRESET_DAYS.map((days) => {
              // Anchor presets to the server's UTC today, not the browser's local date.
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
      <p className="footnote">
        PRs opened or with human activity during the selected period.
        {p.dateLimits && (
          <>
            {" "}Supported dates: {p.dateLimits.earliest_from} – {p.dateLimits.latest_to} (UTC).
            Longer presets are unavailable when history is configured for a shorter period.
          </>
        )}
      </p>
      {p.validationError && (
        <p className="error-text" role="alert">{p.validationError}</p>
      )}
    </section>
  );
}

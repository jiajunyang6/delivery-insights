import type { Snapshot } from "../types";
import { dateRange, timestamp } from "../format";
export function Headline({ snapshot: s }: { snapshot: Snapshot }) {
  return (
    <section className="headline">
      <div className="eyebrow">
        THE DELIVERY PICTURE{" "}
        <span>
          {s.period.days} days · {s.period.from} — {s.period.to}
        </span>
      </div>
      <h1>{s.headline}</h1>
      <div className="freshness">
        <span className="status-dot" />
        As of {timestamp(s.as_of)}
        <span>
          {s.meta.sample.merged_prs} merged PRs ·{" "}
          {s.meta.sample.open_prs_at_as_of} open
        </span>
      </div>
      {!s.meta.comparison_available && (
        <p className="notice">No previous period to compare.</p>
      )}
      {!s.period.complete && s.period.to < dateRange(1).to && (
        <p className="notice">
          Data synced through {timestamp(s.as_of)}; the rest of the period is
          not covered yet.
        </p>
      )}
      <details className="freshness-details">
        <summary>Data freshness</summary>
        {s.meta.data_freshness.map((r) => (
          <p key={r.repo}>
            {r.repo} · {r.last_sync_status} · {timestamp(r.last_synced_at)}
          </p>
        ))}
      </details>
    </section>
  );
}

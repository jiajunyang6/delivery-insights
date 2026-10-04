import type { Audience, Snapshot } from "../types";
import { format, hours, percent, signed } from "../format";
export function Bottlenecks({
  snapshot: s,
  audience,
}: {
  snapshot: Snapshot;
  audience: Audience;
}) {
  const findings =
    audience === "director" ? s.bottlenecks.slice(0, 3) : s.bottlenecks;
  return (
    <section id="bottlenecks">
      <div className="section-heading">
        <div>
          <h2>What to work on next</h2>
          <p>Prioritized waiting patterns and suggested actions, supported by measured evidence.</p>
        </div>
        <span>Ranked by impact on PR time</span>
      </div>
      <details className="metric-guide">
        <summary>How impact and estimates are measured</summary>
        <p>
          PR-hours add time across PRs: two PRs waiting for three hours contribute
          six PR-hours. Each finding's impact compares its affected time with the
          finished PR waiting time: the post-ready waiting time of eligible PRs
          that merged or closed unmerged this period. It excludes open PRs,
          bot and backport PRs and pre-ready coding time, and can include
          waiting that happened before the period. Each percentage is at most
          100%; findings can overlap, so they need not add to 100%. Evidence
          rows such as "share of waiting time spent waiting to merge" use merged
          PRs only, so they can differ from the card's percentage. Severity
          reflects configured evidence thresholds.
        </p>
        <p>
          Capping a stage is an illustrative estimate of the median cycle time
          if waits above the displayed target were shortened. It is not a
          guaranteed improvement or proof of a root cause.
        </p>
      </details>
      {!findings.length && (
        <div className="panel empty">
          {s.meta.sample.merged_prs < 20
            ? audience === "director"
              ? "Too few merged PRs for bottleneck findings."
              : "Too few merged PRs for bottleneck findings; see at-risk PRs."
            : "No bottleneck exceeded the configured evidence thresholds."}
        </div>
      )}
      <div className="finding-grid">
        {findings.map((f) => (
          <article className="finding panel" key={f.id}>
            <div className="finding-top">
              <span className="rank">{String(f.rank).padStart(2, "0")}</span>
              <span className={"badge " + f.severity}>{f.severity}</span>
              <span className="impact">
                {percent(f.impact_share)} of finished PR waiting time
              </span>
            </div>
            <h3>{f.title}</h3>
            <p className="muted">
              {hours(f.impact_pr_hours)} PR-hours affected
            </p>
            <dl className="finding-evidence">
              {f.evidence.map((e) => (
                <div key={e.ref}>
                  <dt>{e.label}</dt>
                  <dd>{format(e.value, e.unit)}</dd>
                </div>
              ))}
            </dl>
            <p className="recommendation">{f.recommendation}</p>
            {f.what_if && (
              <div className="what-if">
                Capping {f.what_if.stage} at {hours(f.what_if.target_hours)} →
                median cycle time{" "}
                <strong>{signed(f.what_if.change_rel)}</strong>
                <small>
                  Illustrative estimate · {f.what_if.affected_prs} PRs affected
                </small>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

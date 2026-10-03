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
    <section>
      <div className="section-heading">
        <h2>What to work on next</h2>
        <span>Ranked by impact on PR time</span>
      </div>
      {!findings.length && (
        <div className="panel empty">
          {s.meta.sample.merged_prs < 20
            ? "Too few merged PRs for bottleneck findings; see at-risk PRs."
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
                {percent(f.impact_share)} of PR time
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

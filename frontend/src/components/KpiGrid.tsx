import type { Metric, Snapshot } from "../types";
import { format, number, signed } from "../format";
function Card({
  label,
  metric: m,
  higher = false,
  note,
  description,
  sampleUnit,
}: {
  label: string;
  metric: Metric;
  higher?: boolean;
  note?: string;
  description: string;
  sampleUnit: string;
}) {
  const improving =
    m.change_rel != null && (higher ? m.change_rel > 0 : m.change_rel < 0);
  return (
    <article className="kpi">
      <div className="kpi-label">{label}</div>
      <p className="kpi-description">{description}</p>
      <div className="kpi-value">{format(m.value, m.unit)}</div>
      <div
        className={
          "delta " +
          (m.change_rel == null || m.significant === false
            ? "neutral"
            : improving
              ? "positive"
              : "negative")
        }
      >
        {m.change_rel != null && (
          <span aria-hidden>
            {m.change_rel > 0 ? "↗ " : m.change_rel < 0 ? "↘ " : "→ "}
          </span>
        )}
        {signed(m.change_rel)}{" "}
        <span className="muted">vs {format(m.previous, m.unit)}</span>
      </div>
      <small>
        n = {number(m.n, 0)} {sampleUnit}
        {note ? " · " + note : ""}
      </small>
      {(m.status === "insufficient_sample" || m.significant === false) && (
        <small className="muted">
          {m.status === "insufficient_sample"
            ? "Insufficient sample"
            : "Not statistically significant"}
        </small>
      )}
    </article>
  );
}
export function KpiGrid({ snapshot: s }: { snapshot: Snapshot }) {
  const e = s.efficiency;
  const days = e.merged_within_n_days.extra.n_days ?? 3;
  return (
    <section aria-labelledby="outcomes-heading">
      <div className="section-heading">
        <div>
          <h2 id="outcomes-heading">Delivery outcomes</h2>
          <p>PRs opened or active this period; each metric uses its own eligible sample.</p>
        </div>
        <span>Compared with the previous {s.period.days} days</span>
      </div>
      <details className="metric-guide">
        <summary>How to read values, changes and sample sizes</summary>
        <p>
          n is the sample size for that card, not the repository's total PR count.
          Median (p50) means half the observations are at or below that value;
          p90 means 90% are at or below it. Averages can be fractional.
        </p>
        <p>
          Changes are relative: (current − previous) / previous × 100%, using
          values before rounding. A percentage metric's relative change is not a
          percentage-point difference. “Not statistically significant” means the
          change did not meet the dashboard's confidence and effect-size checks;
          it does not mean the values are identical. “Insufficient sample” means
          too few observations or qualifying events to report a reliable value.
        </p>
        <p>
          Merged within {days} days includes PRs that became ready for review in
          this period and had at least {days} days of follow-up. Review rounds
          counts transitions back to the author following review feedback,
          rather than individual reviews or comments. Waste excludes PRs closed
          because another PR superseded them.
        </p>
      </details>
      <div className="kpi-grid">
        <Card
          label="Cycle time · median"
          metric={e.cycle_time_p50_hours}
          description="First commit to merge: half of merged PRs finish within this time."
          sampleUnit="merged PRs"
          note={"p90 " + format(e.cycle_time_p90_hours.value, "hours")}
        />
        <Card
          label="Effective throughput"
          metric={e.effective_throughput}
          description="Merged PRs minus reverted PRs and PRs that perform reverts."
          sampleUnit="merged PRs"
          higher
          note="Net shipped PRs"
        />
        <Card
          label={
            "Merged within " +
            days +
            " days"
          }
          metric={e.merged_within_n_days}
          description={`Share merged within ${days} days of becoming ready for review.`}
          sampleUnit="eligible PRs"
          higher
        />
        <Card
          label="Waiting share"
          metric={e.waiting_share}
          description="Share of coding + post-ready time spent awaiting review, CI or merge."
          sampleUnit="merged PRs"
          note="Of cycle time"
        />
        <Card
          label="Waste share"
          metric={e.waste_share}
          description="Closed-unmerged or reverted PRs as a share of closed and merged PRs."
          sampleUnit="closed or merged PRs"
        />
        <Card
          label="Review rounds · average"
          metric={e.avg_review_rounds}
          description="Average times review feedback sends a merged PR back to its author."
          sampleUnit="merged PRs"
        />
        <Card
          label="Review concentration"
          metric={e.review_concentration_top_k}
          description={`Share of reviews performed by the top ${e.review_concentration_top_k.extra.k ?? 2} reviewers.`}
          sampleUnit="reviews"
          note={
            "Top " + (e.review_concentration_top_k.extra.k ?? 2) + " reviewers"
          }
        />
        <Card
          label="Revert rate"
          metric={e.revert_rate}
          description="Share of merged PRs reverted by the report's observation time."
          sampleUnit="merged PRs"
        />
      </div>
    </section>
  );
}

import type { Metric, Snapshot } from "../types";
import { format, number, percent, signed } from "../format";
function Card({
  label,
  metric: m,
  higher = false,
  note,
}: {
  label: string;
  metric: Metric;
  higher?: boolean;
  note?: string;
}) {
  const improving =
    m.change_rel != null && (higher ? m.change_rel > 0 : m.change_rel < 0);
  return (
    <article className="kpi">
      <div className="kpi-label">{label}</div>
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
        {m.status === "insufficient_sample"
          ? "Insufficient sample"
          : m.significant === false
            ? "Not significant"
            : "n = " + number(m.n, 0)}
        {note ? " · " + note : ""}
      </small>
    </article>
  );
}
export function KpiGrid({ snapshot: s }: { snapshot: Snapshot }) {
  const e = s.efficiency;
  return (
    <section aria-labelledby="outcomes-heading">
      <div className="section-heading">
        <h2 id="outcomes-heading">Delivery outcomes</h2>
        <span>Compared with the previous {s.period.days} days</span>
      </div>
      <div className="kpi-grid">
        <Card
          label="Cycle time · median"
          metric={e.cycle_time_p50_hours}
          note={"p90 " + format(e.cycle_time_p90_hours.value, "hours")}
        />
        <Card
          label="Effective throughput"
          metric={e.effective_throughput}
          higher
          note="Net shipped PRs"
        />
        <Card
          label={
            "Merged within " +
            (e.merged_within_n_days.extra.n_days ?? 3) +
            " days"
          }
          metric={e.merged_within_n_days}
          higher
        />
        <Card
          label="Waiting share"
          metric={e.waiting_share}
          note="Of cycle time"
        />
        <Card label="Waste share" metric={e.waste_share} />
        <Card label="Review rounds · average" metric={e.avg_review_rounds} />
        <Card
          label="Review concentration"
          metric={e.review_concentration_top_k}
          note={
            "Top " + (e.review_concentration_top_k.extra.k ?? 2) + " reviewers"
          }
        />
        <Card label="Revert rate" metric={e.revert_rate} />
      </div>
      <div
        className={
          "guardrail " + (s.guardrail.verdict === "ok" ? "" : "warning")
        }
      >
        <div>
          <strong>Quality guardrail</strong>
          <span>
            {s.guardrail.verdict === "ok"
              ? "No detected trade-off"
              : s.guardrail.verdict === "watch"
                ? "Watch quality"
                : "Speed–quality trade-off suspected"}
          </span>
        </div>
        <p>
          Cycle time <b>{signed(s.guardrail.cycle_time_p50_change_rel)}</b>
        </p>
        <p>
          Revert rate <b>{percent(s.guardrail.revert_rate)}</b>{" "}
          <span className="muted">
            ({signed(s.guardrail.revert_rate_change_pp, " pp", 1)})
          </span>
        </p>
      </div>
    </section>
  );
}

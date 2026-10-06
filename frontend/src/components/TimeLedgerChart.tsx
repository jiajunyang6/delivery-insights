/**
 * "Where PR time goes": the insight endpoint's time ledger as stacked shares, previous period
 * against current, with its headline figures.
 */
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Insight, State } from "../types";
import { hours, number, percent, signed, stateColors, stateLabels, states } from "../format";

/** One headline figure with an optional comparison line. */
function Figure({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="figure">
      <dt>{label}</dt>
      <dd>
        <b>{value}</b>
        {detail && <small>{detail}</small>}
      </dd>
    </div>
  );
}

/**
 * Plot each period as one 100% bar split by waiting state; the tooltip adds PR-hours. The
 * previous bar is shown only when a full comparison period exists. Below the chart sit the
 * insight's largest shift, median cycle time and merged PRs; a change is called significant
 * only when the API says so.
 */
export function TimeLedgerChart({ insight: s }: { insight: Insight }) {
  const { largest_change: shift, cycle_time_p50_hours: cycle, merged_prs: merged } =
    s.insight;
  const cycleChange =
    cycle.change_rel == null
      ? `${cycle.n} merged PRs measured`
      : signed(cycle.change_rel) +
        (cycle.significant ? " vs previous" : " vs previous, not significant");
  const data = (
    s.comparison_available ? ["Previous", "Current"] : ["Current"]
  ).map((period) => ({
    period,
    ...Object.fromEntries(
      states.map((state) => [
        state,
        period === "Current"
          ? s.time_ledger.states[state].share
          : (s.time_ledger.states[state].previous_share ?? 0),
      ]),
    ),
  }));
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>Where PR time goes</h2>
          <p>How merged PRs spend their time after becoming ready for review.</p>
          <p>
            {s.period.from} – {s.period.to} (UTC)
            {s.comparison_available &&
              ` compared with ${s.period.compared_to.from} – ${s.period.compared_to.to}`}
            {!s.period.complete && `; data as of ${s.as_of}`}
          </p>
        </div>
        <span>{hours(s.time_ledger.total_pr_hours)} total</span>
      </div>
      <div
        className="chart ledger-chart"
        role="img"
        aria-label="Previous and current waiting-state shares"
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            layout="vertical"
            stackOffset="expand"
            data={data}
            margin={{ top: 8, right: 14, bottom: 0, left: 0 }}
          >
            <CartesianGrid
              strokeDasharray="3 4"
              horizontal={false}
              stroke="#e4e9e7"
            />
            <XAxis
              type="number"
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              allowDataOverflow
              tickFormatter={percent}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="period"
              width={72}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              cursor={false}
              formatter={(v, key, entry) => {
                const state = key as State;
                const duration =
                  entry.payload.period === "Current"
                    ? s.time_ledger.states[state].pr_hours
                    : s.time_ledger.states[state].previous_pr_hours;
                return [
                  percent(Number(v)) +
                    " · " +
                    hours(duration) +
                    " PR-hours",
                  stateLabels[state],
                ];
              }}
            />
            {states.map((state) => (
              <Bar
                key={state}
                dataKey={state}
                stackId="time"
                fill={stateColors[state]}
                barSize={30}
                isAnimationActive={false}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="legend">
        {states.map((state) => (
          <span key={state}>
            <i style={{ background: stateColors[state] }} />
            {stateLabels[state]}{" "}
            <b>{percent(s.time_ledger.states[state].share)}</b>
          </span>
        ))}
      </div>
      <dl className="figures">
        <Figure
          label="Largest shift"
          value={
            shift
              ? `${stateLabels[shift.state]} wait ${signed(shift.change_pp, " pp", 1)}`
              : "—"
          }
          detail={s.comparison_available ? undefined : "No previous period to compare"}
        />
        <Figure label="Median cycle time" value={hours(cycle.value)} detail={cycleChange} />
        <Figure
          label="Merged PRs"
          value={number(merged.value, 0)}
          detail={merged.previous != null ? `Previous ${number(merged.previous, 0)}` : undefined}
        />
      </dl>
      <p className="footnote">
        Reviewer: awaiting review feedback. Author: awaiting author follow-up.
        Merge: approved and awaiting merge. Shares divide
        post-ready PR-hours, excluding coding time; they describe observed waits,
        not proven causes.
      </p>
    </section>
  );
}

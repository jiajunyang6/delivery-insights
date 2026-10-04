import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Snapshot, State } from "../types";
import { hours, percent, stateColors, stateLabels, states } from "../format";
export function TimeLedgerChart({ snapshot: s }: { snapshot: Snapshot }) {
  const data = (
    s.meta.comparison_available ? ["Previous", "Current"] : ["Current"]
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
          <p>Post-ready time · merged PRs</p>
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
      {!s.time_ledger.ci_data_available && (
        <p className="footnote">
          CI data not available. Observed waiting states do not establish
          causation.
        </p>
      )}
    </section>
  );
}

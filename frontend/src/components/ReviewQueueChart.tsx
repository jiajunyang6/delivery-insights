import { chartColors } from "../format";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Week } from "../types";
export function ReviewQueueChart({ weeks }: { weeks: Week[] }) {
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>Review demand & capacity</h2>
          <p>
            PRs opened or active this period: weekly arrivals, first reviews and
            open queue
          </p>
        </div>
      </div>
      <div
        className="chart queue-chart"
        role="img"
        aria-label="Weekly review inflow, outflow and open queue"
      >
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={weeks}
            margin={{ top: 10, right: 12, bottom: 0, left: 0 }}
          >
            <CartesianGrid
              strokeDasharray="3 4"
              vertical={false}
              stroke={chartColors.grid}
            />
            <XAxis
              dataKey="week_start"
              tickFormatter={(v) => String(v).slice(5)}
              axisLine={false}
              tickLine={false}
            />
            <YAxis width={40} axisLine={false} tickLine={false} />
            <Tooltip />
            <Legend />
            <Bar
              dataKey="inflow"
              name="Inflow"
              fill={chartColors.inflow}
              radius={[3, 3, 0, 0]}
              isAnimationActive={false}
            />
            <Bar
              dataKey="outflow"
              name="First reviews"
              fill={chartColors.outflow}
              radius={[3, 3, 0, 0]}
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="open_at_week_end"
              name="Open queue"
              stroke={chartColors.queue}
              strokeWidth={2}
              dot={{ r: 3 }}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

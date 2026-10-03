import type { Location } from "../types";
import { format, hours, number, percent } from "../format";
export function LocationsTable({ locations }: { locations: Location[] }) {
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>Where waits concentrate</h2>
          <p>By area, owner rule or directory</p>
        </div>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Location</th>
              <th>Merged</th>
              <th>Pickup p50</th>
              <th>vs rest</th>
              <th>Reviewer-wait share</th>
              <th>In / out</th>
              <th>At risk</th>
              <th>Owners</th>
            </tr>
          </thead>
          <tbody>
            {locations.map((l) => (
              <tr key={l.location}>
                <th scope="row">{l.location}</th>
                <td>{number(l.merged_prs, 0)}</td>
                <td>{hours(l.pickup_p50_hours)}</td>
                <td>{format(l.pickup_ratio_vs_rest, "ratio")}</td>
                <td>
                  <div className="cell-share">
                    <span
                      style={{ width: percent(l.waiting_reviewer_share) }}
                    />
                  </div>
                  {percent(l.waiting_reviewer_share)}
                </td>
                <td>
                  {l.inflow} / {l.outflow}
                </td>
                <td>{l.at_risk_prs}</td>
                <td>{number(l.owners_count, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!locations.length && (
        <p className="empty">No merged PRs in this period.</p>
      )}
    </section>
  );
}

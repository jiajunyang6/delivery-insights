/** Configuration notice naming the `.env` variable to fix. */
import { setupItems } from "../setup";
import type { SetupStatus } from "../types";

/** Show setup problems as an alert and information as a status; render nothing when healthy. */
export function SetupNotice({ setup }: { setup: SetupStatus | null }) {
  const items = setup ? setupItems(setup) : [];
  if (!items.length) return null;
  const problems = items.some((i) => i.level === "problem");
  return (
    <section
      className={"setup-notice " + (problems ? "problem" : "info")}
      role={problems ? "alert" : "status"}
      aria-label="Configuration"
    >
      <strong>Configuration</strong>
      <ul>
        {items.map((item) => (
          <li key={item.text}>{item.text}</li>
        ))}
      </ul>
      <small>
        After editing .env, run <code>docker compose up -d</code> so the containers pick up
        the change, then refresh this page.
      </small>
    </section>
  );
}

import { setupItems } from "../setup";
import type { SetupStatus } from "../types";

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
        the change.
      </small>
    </section>
  );
}

/** Sync notice for the selected repository when its last sync failed. */
import { syncItem } from "../setup";
import type { RepoStatus } from "../types";

/** Show the failed sync's error code and whether a retry is running; nothing otherwise. */
export function SyncNotice({ repo }: { repo: RepoStatus | undefined }) {
  const item = syncItem(repo);
  if (!item) return null;
  const problem = item.level === "problem";
  return (
    <section
      className={"setup-notice " + item.level}
      role={problem ? "alert" : "status"}
      aria-label="Sync"
    >
      <strong>Sync</strong>
      <p>{item.text}</p>
    </section>
  );
}

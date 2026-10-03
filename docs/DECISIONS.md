# Decisions

Implementation decisions that deviate from, or fill gaps in, [the plan](plan/00-overview.md).
This is the current set; superseded entries remain in Git history.

## Product scope

| Decision | Reason |
|---|---|
| Every dashboard section and API metric uses the **period-active cohort**: PRs opened, or with identified non-bot activity, in the period. Comparisons apply the same rule to the previous period. There is no all-open toggle. | A sprint-sized view is the main use case, and one consistent scope is simpler to read. The cost is that idle older PRs only appear in a longer period (see NOTES, next steps). |
| Review-queue findings use flow (weekly demand vs first reviews, `net_inflow_share`), not open-queue growth. | Period cohorts omit inactive older PRs, so stock growth would be biased upward. |
| The narrative is English-only: `lang=en` is the only accepted value; others return 422. | Product decision; it removes a second language from prompts, templates and validators. |
| Default `BACKFILL_DAYS` is 120. | Gives the 60-day view a full previous period while keeping the first sync within GitHub rate limits. |
| Abstention has three reasons: `no_slowdown`, `insufficient_signal` and `no_comparison`. Abstained narratives also state where PR time goes now. Hypothesis chains list only stages and areas that carry the added time. | A constant "signals are insufficient" message carried no information. Scores, gates and thresholds are unchanged. |
| The API and UI bind to localhost. | The plan excludes authentication; this is a local demo. |

## Analytics and narrative

| Decision | Reason |
|---|---|
| Closed intervals of reopened PRs are excluded from waiting time but kept in elapsed milestones. | Separates active waiting from calendar time, as the plan distinguishes. |
| Revert, reland and supersession links are stored; chain-level delivery time is deferred. | Explicit plan deferral; cycle time stays per PR. |
| Measures without a plan minimum return null below 10 observations (author-WIP correlation, post-approval CI overlap). Missing CI is never treated as zero. | Avoids misleading small-sample numbers. |
| Analytics code has no I/O; the database loader lives in `db/dataset.py`. Window cohorts are memoized per dataset. | Keeps analytics pure and testable, and avoids repeated cohort scans without changing output. |
| Concurrent identical snapshot requests may compute twice; Postgres deduplicates. | Identical inputs give identical bytes, so no distributed lock is needed on the read path. |
| Abstention words exempt a causal sentence from the hedge check only when no hypothesis is output. | Otherwise "insufficient" could bypass a low-confidence wording ceiling. |
| The prompt is versioned (now v7); every version change invalidates cached narratives. Failed real-Bedrock prompt versions stay in EVALUATION.md. Validators and gate thresholds were never relaxed to pass. | Prompt changes were driven by real failures; recording them keeps the evaluation honest. |
| Confidence is reported as performance on planted synthetic effects only. The synthetic size scenario scales work duration by √(size multiplier). | The score expresses evidence strength, not causal probability. The scaling is an explicit fixture assumption so the planted mechanism is observable. |

## Sync and operations

| Decision | Reason |
|---|---|
| A malformed PR is skipped and logged (repo, number, exception type). NULs are stripped. Timelines that break invariants are kept and counted in job stats. | One bad upstream record must not block a repository. |
| GraphQL queries omit `Team.slug` for requested teams. | A public-repository token gets `INSUFFICIENT_SCOPES` for it, and team names do not affect analytics. |
| Stale PR pagination after a sync clears rows and offers a refresh; pagination requests revalidate the cache. | A sync changes the snapshot identity, so old cursors are rejected. |
| The worker restarts unless stopped. Logs are JSON across uvicorn, arq and the app, and exceptions log their type only. | Observed arq exit on Redis loss; avoids leaking upstream error text. |
| One backend image build is reused by api, worker and migrate. nginx re-resolves the API address every 10 s. | Parallel builds of one tag failed; a recreated API container caused 502s. |
| Vite 8, plugin-react 6 and Node 24 (React 18, Recharts 2 retained). `make eval` loads the root `.env`. | The planned Vite 5 tree had security advisories, and Node 20 is end-of-life. Credentials stay out of code and logs. |

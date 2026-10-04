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
| Retained measures apply their existing sample gates. Missing CI is never treated as zero; unused author-WIP and post-approval overlap outputs were removed in analytics 1.5.0. | Avoids misleading small-sample numbers and unused computation. |
| Analytics code has no I/O; the database loader lives in `db/dataset.py`. Window cohorts are memoized per dataset. | Keeps analytics pure and testable, and avoids repeated cohort scans without changing output. |
| Concurrent identical snapshot requests may compute twice; Postgres deduplicates. | Identical inputs give identical bytes, so no distributed lock is needed on the read path. |
| Abstention words exempt a causal sentence from the hedge check only when no hypothesis is output. | Otherwise "insufficient" could bypass a low-confidence wording ceiling. |
| The prompt is versioned (now v7); every version change invalidates cached narratives. Failed real-Bedrock prompt versions stay in EVALUATION.md. Validators and gate thresholds were never relaxed to pass. | Prompt changes were driven by real failures; recording them keeps the evaluation honest. |
| Confidence is reported as performance on planted synthetic effects only. The synthetic size scenario scales work duration by √(size multiplier). | The score expresses evidence strength, not causal probability. The scaling is an explicit fixture assumption so the planted mechanism is observable. |

| Analytics 1.5.0 removes only unused snapshot outputs and keeps strict schemas. Cache/snapshot IDs use the current version; bootstrap seeds use the unchanged canonical parameters with `analytics_version=1.4.0`. | Prevents accidental changes to confidence intervals, significance, headlines and narrative scoring when trimming the payload. |
| Shared cache/filter/error helpers live below API routes in `snapshots/`; pointer resolution stays independent, and CI mapping helpers used only by synthetic eval live in the eval pipeline. | Removes reverse and circular imports without merging modules that have distinct responsibilities. |
| `HYPOTHESES` stores card titles and template subjects separately. Evidence is extracted once and generation enforces one deadline across the original call and repair. | Removes duplicated rules while retaining wording, attempts, fallback, cache and concurrency behavior. |
| Storage migration consolidation and column deletion remain deferred until stages 0–8 are merged to main and the current local-data deletion is explicitly confirmed. | The reset deletes collected data; the refactor branch preserves the existing migrations and volumes. |

## Sync and operations

| Decision | Reason |
|---|---|
| A malformed PR is skipped and logged (repo, number, exception type). NULs are stripped. Timelines that break invariants are kept and counted in job stats. | One bad upstream record must not block a repository. |
| Sync prefetches at most one page while committing the current page. Cursors and coverage advance only after successful writes. | Overlaps network and database work while retaining resumable checkpoints; abandoned downloads are cancelled. |
| After two successful PR pages, adaptive pagination restores the configured page size (default 25). Any page failure resets the streak. | A transient GraphQL failure should not leave the entire sync at a reduced page size. |
| Normal sync links PRs when inserted or changed PRs set the durable `links_pending` flag; unchanged pages skip the scan. Linking clears the flag transactionally, and explicit rederivation still rebuilds links. | Existing PR merges, closures and reference changes affect links too. A conservative changed-content trigger preserves correctness and pending work across failures. |
| Incoming PR versions older than the stored `updated_at` are ignored before replacing raw data or derived facts; the upsert also guards the timestamp. | A prefetched backfill page must not overwrite a newer version written by checkpoint catch-up. Equal timestamps with changed content remain eligible. |
| Revision 0003 marks populated repositories for one-time link reconciliation, including databases already upgraded to 0002. Its downgrade retains pending work. | Legacy interrupted jobs have no reliable linking-completion marker. A forward migration repairs the bootstrap without rewriting an applied schema revision or losing existing data. |
| Default snapshot precomputation covers 7, 30 and 60 days. Snapshot orchestration lives in `snapshots/service.py`; `FakeLLMClient` lives in `tests/fakes.py`. | Matches dashboard presets, gives shared API/worker orchestration a package, and keeps test doubles out of production. |
| GraphQL queries omit `Team.slug` for requested teams. | A public-repository token gets `INSUFFICIENT_SCOPES` for it, and team names do not affect analytics. |
| Stale PR pagination after a sync clears rows and offers a refresh; pagination requests revalidate the cache. | A sync changes the snapshot identity, so old cursors are rejected. |
| The worker restarts unless stopped. Logs are JSON across uvicorn, arq and the app, and exceptions log their type only. | Observed arq exit on Redis loss; avoids leaking upstream error text. |
| One backend image build is reused by api, worker and migrate. nginx re-resolves the API address every 10 s. | Parallel builds of one tag failed; a recreated API container caused 502s. |
| Vite 8, plugin-react 6 and Node 24 (React 18, Recharts 2 retained). `make eval` loads the root `.env`. | The planned Vite 5 tree had security advisories, and Node 20 is end-of-life. Credentials stay out of code and logs. |

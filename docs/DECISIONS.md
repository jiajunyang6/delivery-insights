# Decisions

Implementation decisions that deviate from, or fill gaps in, [the plan](PLAN.md).
This is the current set; superseded entries remain in Git history.

## Product scope

| Decision | Reason |
|---|---|
| Every dashboard section and API metric uses the **period-active cohort**: PRs opened, or with identified non-bot activity, in the period. Comparisons apply the same rule to the previous period. There is no all-open toggle. | A sprint-sized view is the main use case, and one consistent scope is simpler to read. The cost is that idle older PRs only appear in a longer period (see NOTES, next steps). |
| Review-queue evidence counts weeks in which ready arrivals outnumber first reviews (flow), not open-queue growth. | Period cohorts omit inactive older PRs, so stock growth would be biased upward. |
| The narrative is English-only and written for one audience; its route takes no language or audience parameter. | Product decision; it removes a second language and audience from prompts, templates and validators. |
| The dashboard shows only the narrative, the time ledger, repository and date selection, and sync progress with configuration help. The headline, KPI cards, findings, review queue, area table and at-risk PRs, with their endpoints, were removed, and analytics keeps only what the narrative cites. | The delivered scope must be fully explainable: every remaining number feeds a visible section or a narrative citation. |
| Default `BACKFILL_DAYS` is 120. | Gives the 60-day view a full previous period while keeping the first sync within GitHub rate limits. |
| Abstention has three reasons: `no_slowdown`, `insufficient_signal` and `no_comparison`. Abstained narratives also state where PR time goes now. Hypothesis chains list only stages and areas that carry the added time. | A constant "signals are insufficient" message carried no information. Scores, gates and thresholds are unchanged. |
| The API and UI bind to localhost. | The plan excludes authentication; this is a local demo. |

## Analytics and narrative

| Decision | Reason |
|---|---|
| Closed intervals of reopened PRs are excluded from waiting time but kept in elapsed milestones. | Separates active waiting from calendar time, as the plan distinguishes. |
| Revert, reland and supersession links, ownership rules and the PR fields they needed (body, head branch, merge commit, author association) are not collected or derived. Locations use labels, then directories. | No remaining metric reads them; linking rescanned every PR in a repository at each checkpoint, and CODEOWNERS needed a separate enrichment job. |
| Retained measures apply their existing sample gates. Missing CI is never treated as zero; unused author-WIP and post-approval overlap outputs were removed in analytics 1.5.0. | Avoids misleading small-sample numbers and unused computation. |
| Analytics code has no I/O; the database loader lives in `db/dataset.py`. Window cohorts are memoized per dataset. | Keeps analytics pure and testable, and avoids repeated cohort scans without changing output. |
| Concurrent identical snapshot requests may compute twice; Postgres deduplicates. | Identical inputs give identical bytes, so no distributed lock is needed on the read path. |
| The quality trade-off hypothesis was removed, leaving three slowdown hypotheses. Every eligible hypothesis is shown, and a faster cycle time abstains with `no_slowdown`. Review capacity no longer uses the at-risk location share. | The quality hypothesis never fired on the tracked repository and alone needed revert and approval signals; the at-risk share was the only consumer of waiting-state baselines. |
| Abstention words exempt a causal sentence from the hedge check only when no hypothesis is output. | Otherwise "insufficient" could bypass a low-confidence wording ceiling. |
| The prompt is versioned (now v11); every version change invalidates cached narratives. Failed real-Bedrock prompt versions stay in EVALUATION.md. Validators and gate thresholds were never relaxed to pass. | Prompt changes were driven by real failures; recording them keeps the evaluation honest. |
| Confidence is reported as performance on planted synthetic effects only. The synthetic size scenario scales work duration by √(size multiplier). | The score expresses evidence strength, not causal probability. The scaling is an explicit fixture assumption so the planted mechanism is observable. |

| Analytics 1.6.0 removes snapshot outputs without a remaining consumer and keeps strict schemas. Cache/snapshot IDs use the current version; bootstrap seeds use the unchanged canonical parameters with `analytics_version=1.4.0`, and retained metrics keep their sampling names. | Prevents accidental changes to confidence intervals, significance and narrative scoring when trimming the payload. |
| Shared cache/error helpers live below API routes in `snapshots/`; pointer resolution stays independent, and CI mapping helpers used only by synthetic eval live in the eval pipeline. | Removes reverse and circular imports without merging modules that have distinct responsibilities. |
| `HYPOTHESES` stores card titles and template subjects separately. Evidence is extracted once and generation enforces one deadline across the original call and repair. | Removes duplicated rules while retaining wording, attempts, fallback, cache and concurrency behavior. |
| Migrations are consolidated into one initial revision because the project is unreleased. Stage 9 runs on `optimization-v2`; the user explicitly waived merging stages 0–8 into main. Old databases require a confirmed local reset and resync. | Removes unused columns and old upgrade paths. The current-time confirmation before deleting collected volumes still applies. |

## Sync and operations

| Decision | Reason |
|---|---|
| A malformed PR is skipped and logged (repo, number, exception type). NULs are stripped. Timelines that break invariants are kept and counted in job stats. | One bad upstream record must not block a repository. |
| Sync prefetches at most one page while committing the current page. Cursors and coverage advance only after successful writes. | Overlaps network and database work while retaining resumable checkpoints; abandoned downloads are cancelled. |
| After two successful PR pages, adaptive pagination restores the configured page size (default 25). Any page failure resets the streak. | A transient GraphQL failure should not leave the entire sync at a reduced page size. |
| Incoming PR versions older than the stored `updated_at` are ignored before replacing raw data or derived facts; the upsert also guards the timestamp. | A prefetched backfill page must not overwrite a newer version written by checkpoint catch-up. Equal timestamps with changed content remain eligible. |
| Default snapshot precomputation covers 7, 30 and 60 days. Snapshot orchestration lives in `snapshots/service.py`; `FakeLLMClient` lives in `tests/fakes.py`. | Matches dashboard presets, gives shared API/worker orchestration a package, and keeps test doubles out of production. |
| GraphQL queries omit `Team.slug` for requested teams. | A public-repository token gets `INSUFFICIENT_SCOPES` for it, and team names do not affect analytics. |
| The worker restarts unless stopped. Logs are JSON across uvicorn, arq and the app, and exceptions log their type only. | Observed arq exit on Redis loss; avoids leaking upstream error text. |
| One backend image build is reused by api, worker and migrate. nginx re-resolves the API address every 10 s. | Parallel builds of one tag failed; a recreated API container caused 502s. |
| Vite 8, plugin-react 6 and Node 24 (React 18, Recharts 2 retained). `make eval` loads the root `.env`. | The planned Vite 5 tree had security advisories, and Node 20 is end-of-life. Credentials stay out of code and logs. |

# Technical reference

Details behind the [README](../README.md), which covers running the stack, the architecture
tour, the metric choice, the API overview and the main settings.

## Metric semantics

The narrative draws on cycle time, first-review wait, review rounds, the review queue and
review concentration, PR size, the weekly series and an accounting attribution of the added
hours. They appear on the page only as cited evidence. Relative changes use unrounded values,
so recomputing them from displayed values can differ slightly. These are PR-flow signals, not
deployment lead time, DORA change failure rate, individual productivity scores or causal
proof. Workflow and timestamp changes affect them.

The time ledger covers the post-ready waiting time of merged PRs only: no bot or backport PRs
and no pre-ready coding time.

## Pipeline

1. **Sync:** staged backfill, overlapping incremental windows and open-PR sweeps write
   idempotent batches. Coverage and success checkpoints advance after phase completion; the
   resumable backfill cursor is saved with each batch.
2. **Derive:** an event-driven state machine creates non-overlapping intervals and per-PR
   facts. A PR is located by its matching labels, otherwise by its most-touched directories.
   Version changes rederive stored PRs and enqueue snapshot precomputation.
3. **Snapshot:** cached or persisted snapshots are reused. On a miss, a read-only
   repeatable-read transaction loads a consistent dataset for pure analytics, deterministic
   bootstrap comparisons and canonical JSON; the result is persisted in a separate write
   transaction. Parameters, versions and watermarks set identity.
4. **Narrative:** described in
   [Narrative, confidence and evidence chain](#narrative-confidence-and-evidence-chain). The
   validator and template run inside the API process.

| Component | Responsibility |
|---|---|
| `api` | Local-data queries, snapshot/narrative persistence |
| `worker` | GitHub ingestion, derivation, precomputation, retention |
| `postgres` | Source data, facts, intervals, immutable snapshots and narratives |
| `redis` | arq jobs, locks, response caches and fixed-window limits |
| `web` | React dashboard and nginx same-origin proxy |
| `migrate` | One-shot Alembic schema upgrade before API/worker startup |

Analytics has no database or HTTP imports; the I/O loader lives in `db/dataset.py`. The API
process does not import source adapters or call GitHub. It may call Bedrock for an uncached
narrative; snapshot computation and boto3 calls run in threads.

## API

OpenAPI is available at `/openapi.json` and `/docs`. `as_of` is capped by the least recent
repository sync watermark. `repo=` names exactly one tracked repository; unknown parameters
are ignored. Problem responses include `request_id` and sanitized detail.

Once sync has produced a `200` response:

```bash
API=http://localhost:8000
TO=$(python3 -c 'import datetime as d; print(d.datetime.now(d.timezone.utc).date())')
FROM=$(python3 -c 'import datetime as d; print(d.datetime.now(d.timezone.utc).date()-d.timedelta(days=29))')
curl -sD /tmp/di-headers "$API/v1/insights/delivery?repo=bevyengine/bevy&from=$FROM&to=$TO" -o /tmp/di-insight.json
SID=$(python3 -c 'import json; print(json.load(open("/tmp/di-insight.json"))["snapshot_id"])')
ETAG=$(python3 -c 'from pathlib import Path; print(next(s.split(":",1)[1].strip() for s in Path("/tmp/di-headers").read_text().splitlines() if s.lower().startswith("etag:")))')
curl -i -H "If-None-Match: $ETAG" "$API/v1/insights/delivery?repo=bevyengine/bevy&from=$FROM&to=$TO"
curl -s "$API/v1/snapshots/$SID/narrative"
```

Insights use `private, no-cache`, so browsers revalidate with the ETag and see new coverage as
soon as a sync lands. Successful narratives and disabled-LLM templates have
`private, max-age=3600`; failure fallbacks use `no-store`. Internal Redis TTLs are separate
from HTTP cache directives.

A `202` is a Pending object, not an insight. Inspect each repo's `reason` and `job` (status
and phase). The UI polls for at most five minutes, then asks for a later refresh.

## Insight contract

The strict `Insight` model in `api/schemas.py` is the public field contract
(`extra="forbid"`). `analytics/insight.py` projects it from the snapshot:

| Field | Contents |
|---|---|
| `snapshot_id`, `repo`, `period`, `as_of`, `comparison_available` | Identity, dates, comparison window and observation cutoff |
| `insight.statement` | Deterministic factual summary; it names the largest wait and its change, never a cause |
| `insight.largest_wait` | State with the largest share of post-ready waiting time; `null` when no merged PR waited |
| `insight.largest_change` | State whose share moved most in percentage points; `null` without a comparison or movement |
| `insight.cycle_time_p50_hours`, `insight.merged_prs` | Median cycle time with its significance and sample; merged PR count |
| `time_ledger` | Reviewer/author/merge PR-hours and shares, current and previous |
| `links.narrative` | Narrative route for this snapshot |

When the largest wait is also the largest shift, the statement reports both in one sentence.
The ETag is computed over the insight bytes; the view is deterministic for a snapshot.

## Snapshot (internal)

The snapshot is stored in Postgres and Redis and read server-side by the narrative; the API
never returns it. It holds the time ledger and every metric the narrative evidence catalog
reads, and nothing else. Evidence items in the narrative response keep a `ref` JSON pointer
into this snapshot for audit; the dashboard does not display it.

| Block | Contents | Consumers |
|---|---|---|
| `period`, `as_of`, `repos`, `meta` | Dates, comparison window, observation cutoff, versions, merged sample | Insight view, evidence pack |
| `time_ledger` | Reviewer/author/merge PR-hours and shares, current and previous | Insight view, E18, E19, E21 |
| `efficiency` | Cycle p50, merged PRs, first-review p50, review rounds, post-review commits, review concentration, PR size p50, large-PR share | Insight view (cycle, merged), E1, E3, E8, E9, E15, E24, E30, E31 |
| `bottleneck_analysis` | Review-queue imbalance weeks, location first-review ratios and reviewer wait | E22, location items |
| `drivers`, `trend` | Slowest-decile size ratio; attribution of added hours by state, location and large PRs | E37, E42, E48, location items |
| `series` | Weekly current and previous values | Effect size and persistence |

Bootstrap seeds are frozen by `SAMPLING_SEED_VERSION`, so sampling stays stable across
releases. Old snapshots and narrative caches stay isolated by version; workers rederive stored
PRs in the background.

## Narrative, confidence and evidence chain

Confidence is a **deterministic evidence-strength score**, not a probability.
It has not been calibrated against real repository outcomes or hand-labeled causes.
Correlations and queue pressure are reasons to investigate, not proof.

The model sees structured numbers, evidence IDs and sanitized repository/location names.
Code supplies evidence references, sample counts, changes and eligible hypotheses from this
library:

| Hypothesis | Symptom | Mechanism to look for |
|---|---|---|
| Review capacity | More time waiting for review | Demand, concentration and localized first-review delay |
| PR size growth | Longer cycle or author/review stages | Larger changes with extra review/rework |

```text
score = 0.30*S + 0.20*E + 0.20*P + 0.15*N + 0.15*L - 0.15*C
```

| Factor | Meaning |
|---|---|
| S | Fraction of evaluable symptom/mechanism signals present |
| E | Effect size of the increase against previous weekly variability |
| P | Fraction of current weeks supporting the change |
| N | `min(1, main_metric_sample_size / 100)` |
| L | Localization/concentration, defined for each hypothesis |
| C | Number of counter-evidence groups present |

Example covered by tests: S=3/4, E=1, P=10/13, N=61/100, L=0.63, C=0
gives 0.7648, rounded to **0.76 (high)**. One counter-evidence group yields **0.61 (medium)**.
No mechanism signal means no hypothesis, even when symptoms look strong. Every eligible
hypothesis is shown, ordered by score; a faster cycle time abstains with `no_slowdown`.
Each candidate in the pack carries `explains`, the changes its present symptoms record, such
as "the larger share of PR time waiting on reviewers". Cause sentences name that change
(in the prompt and the template), so a candidate triggered by reviewer wait is never presented
as the cause of a cycle-time change it does not cover.

| Score after caps and rounding | Level | Wording |
|---|---|---|
| At least 0.75 | high | likely |
| Greater than 0.50, below 0.75 | medium | may / might / possibly / could |
| 0.35 through 0.50 | low | There are early signs that ...; no likely/may/might/possibly/could |
| Below 0.35, missing comparison or mechanism | abstain | `no_slowdown`, `insufficient_signal` or `no_comparison` sentence |

Validators check schema, IDs, sentence-local numeric grounding, units, direction,
citation coverage, personal identifiers and causal wording ceilings across the whole body.
An LLM may downgrade a candidate, never raise its deterministic score or wording level.
At most one outside-library hypothesis is allowed and fixed at low confidence (0.35).
Inspect `meta.generated_by`, `validation`, `attempts`, `fallback_reason` and `violations`.
In the UI, citation buttons scroll to and highlight the corresponding evidence entry.

Review-queue evidence (E22) displays the number of weeks with more PRs becoming ready for
review than receiving a first review, out of `extra.weeks_total`. Weeks are Monday-aligned
UTC buckets clipped to the report window, so partial weeks count. First reviews can serve
PRs that became ready earlier; this is a flow comparison, not an unreviewed-PR count.
Review-concentration evidence (E24) displays the actual `extra.k` in its label. Reviewers
are ranked by human review-event counts separately in each period; repeated reviews of a
PR count separately, and the top reviewers can differ between periods. The same labels
and values appear in hypothesis evidence chips, including for cached narrative responses.

## Additional settings

The README lists the main settings. These have working defaults in `.env.example`:

| Variable | Default | Purpose |
|---|---|---|
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed browser origins |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per-client-IP fixed-window limit on `/v1` |
| `DATABASE_URL` | Compose Postgres URL | Async SQLAlchemy database connection |
| `REDIS_URL` | `redis://redis:6379/0` | Cache, job queue and locks |
| `LOG_LEVEL` | `INFO` | Application logging level |

The current implementation targets public repositories; private-repo authorization is not
verified.

## Operations

`/v1/repos` also returns `setup`: whether `GITHUB_TOKEN` and `AWS_BEARER_TOKEN_BEDROCK` are
set (never their values), sync statuses that point to configuration (`missing_token`,
`auth_error`, `not_found`), the Bedrock region and model ID, and the latest Bedrock error code.
That code is cleared after the next successful call and ignored once `BEDROCK_MODEL_ID` or
`AWS_REGION` changes. A connection error (`EndpointConnectionError`) means the endpoint was not
reached, so the model ID was not checked; an invalid model ID returns `ValidationException`.
The API checks these settings once at startup with a one-token Bedrock request, because cached
narratives make no Bedrock call and would otherwise hide a bad key until a new snapshot needs
wording. The dashboard turns these into a Configuration notice that names the `.env` variable
to fix.

Read `/v1/repos` before judging missing data. Snapshots/narratives are retained for seven days
and sync-job records for thirty days; source PR records are retained until the database is
reset. Worker housekeeping expires retained data daily.

The worker syncs on a schedule (`SYNC_INTERVAL_MINUTES`); there is no manual sync endpoint.
Requests proxied through nginx share its upstream client-IP bucket; this is a local demo.
Cache/rate-limit Redis failures are fail-open where possible; readiness still reports failure.
Worker locks are renewed; checkpoints support resumption. Compose restarts failed workers.

Access logs are JSON with `request_id`, `method`, `path`, `status` and `duration_ms`.
Worker application events include `job`, `repo` and `phase`; CLI bootstrap logs are JSON too.
Snapshot logs separate `duration_ms`, `load_ms`, `compute_ms` and `merged_prs`.
Tokens, headers, raw upstream responses and prose are not logged; exceptions expose only type.

| Symptom | What to inspect |
|---|---|
| Persistent 202 | Pending `reason`/`job`, repository coverage and sync status |
| `rederive` | A configuration/version change requires complete fact rederivation; failed jobs retry next sync |
| `stale` | The consistent sync watermark has not reached the requested period |
| `missing_token` / 503 | Add GitHub credentials and recreate API/worker with the environment |
| Always template | `meta.fallback_reason`, model access, validator violations and sanitized logs |
| 404 for an old snapshot | Seven-day retention expired; request a new insight |
| Incomplete comparison | More history is needed for the previous equal-length period |
| Unexpected numbers | Sync job `skipped_prs` and `invariant_violations` counters and their structured warnings |

Raw GitHub node IDs and actor types remain in the query for timeline paging and bot
detection. The schema stores only the PR fields and facts that analytics reads: no PR bodies,
head branches, merge commits, author associations, ownership rules or link fields.
`UNIQUE(pr_id, seq)` also serves interval lookups by PR; no performance claim is made without
representative EXPLAIN measurements. There is no authenticated administrative UI or backup
orchestration in this demo.

## Security

- Secrets are environment-only `SecretStr` values; `.env` and local artifacts are ignored.
- Query/path inputs use allowlists. Repositories must be tracked. GitHub base URLs come
  only from trusted configuration; the source client uses GraphQL only.
- SQLAlchemy statements are parameterized; SQL `text()` usage is limited to static statements/defaults.
- The evidence pack excludes raw PR text and user names; unsafe location names are replaced.
- Problem responses omit tracebacks and upstream exception text.
- React renders all server text as text and builds no links from API data.
- nginx sets CSP, `nosniff` and `no-referrer`. There are no external scripts or fonts.
- Application containers run as UID 10001; web runs as UID 101.
- Loopback binding limits the demo's exposure. Production requires authentication,
  authorization, TLS, secret rotation and an appropriate deployment security review.

## Testing and evaluation

Tests focus on accounting boundaries and failure behavior, not only happy-path output.
They cover 22 specified timeline cases plus 200 random invariant sequences, deterministic
golden output under shuffled input, sample gates, drivers,
resumable ingestion, snapshot identity/expiry, privacy, validators and fallback races.
Integration tests use actual Postgres 16 and Redis 7 through Testcontainers; GitHub and
Bedrock calls are mocked or SDK-stubbed. Docker must be running for the full suite.

The evaluation harness runs three planted scenarios (review capacity, PR size growth and no
signal) with five seeds: 15 English narratives on prompt v13. Numeric, citation and hedge
consistency cover final LLM outputs.

| Metric | Offline (stub) | Real Bedrock (Sonnet 4.6) | Required |
|---|---|---|---|
| First-attempt validity | 15/15 | 14/15 (0.93) | ≥ 0.90 |
| Numeric / citation / hedge consistency | 15/15 each | 15/15 each | 1.00 each |
| Root-cause hit rate | 10/10 | 10/10 | ≥ 0.80 |
| No-signal abstention | 5/5 | 5/5 | ≥ 0.80 |
| High-confidence precision | 10/10 | 10/10 | ≥ 0.80 |
| Fallback rate | 0/15 | 0/15 | ≤ 0.10 |

The one invalid real first answer quoted an unsupported number and passed after its single
repair. On live Bevy data, the 60-day narrative with two low-band hypotheses passed validation
on the first attempt in 4/4 generations. The synthetic suite was used during prompt
development; it is not a held-out benchmark or real-world causal calibration. The size
scenario scales synthetic work duration with the square root of the planted size multiplier,
an explicit fixture assumption. Validators and gate thresholds were never relaxed to pass.
Missing-key evaluation exits 2.

## Trade-offs and limitations

The README lists the main trade-offs (freshness, time basis, locations, constraints) and the
largest items left out. Further trade-offs:

| Decision | Choice and cost | Follow-up |
|---|---|---|
| Scope | Default-branch flow; bots/backports excluded | Separate release-branch view |
| Sources | Whitelisted GitHub repos only | Extend `SourceAdapter` with shared normalized records |
| Snapshot compute | Concurrent cold requests can duplicate deterministic work | Add single-flight only if measured necessary |
| Commit times | Committer timestamps approximate push/revision time | Collect push events |
| Reopened PRs | Closed intervals excluded from ledger; elapsed milestones retain them | Review prevalence before changing duration semantics |
| Confidence | Evidence score tested only on synthetic scenarios | Replay history and calibrate with human labels |

Also not done: arbitrary repo/org discovery, LLM arithmetic, self-assigned LLM confidence,
raw-text prompts and a business-hours mode. Revert, reland and supersession links are not
derived. Real-history backtesting, human calibration, AI-authorship analysis, stacked-PR
dependency waiting and cumulative-flow charts remain outside this version.

Known limitations:

- PRs opened before the selected period and merged by a bot with no human activity in the
  period are not counted in throughput.
- GitHub omits design discussions, offline coordination and deployments. Rewritten or rebased
  commit timestamps distort coding time. Current labels are not historical ownership.
- Small samples suppress p50 below 20 and rates below 30 cases/5 events. Location and PR-size
  comparisons have their own sample gates; insufficient values remain null.
- Recharts 2 is deprecated; migrating to v3 is a maintenance follow-up.

## Code organization

The delivery insight and narrative routes, including HTTP reply conversion, live in
`api/routes/insights.py`. The PR-size driver shares `analytics/efficiency.py`; the stable
numbered catalog shares `narrative/evidence.py`; `sources/__init__.py` exports the source
protocol. Health and repository routes remain separate. Analytics input records contain
only consumed fields; the loader does not select PR titles, URLs, authors or draft flags.

Frontend `api.ts` owns fetch/polling and the `useAbortable` effect, which cancels superseded
requests and requests still active on unmount. `format.ts` owns display and UTC date helpers.
TypeScript rejects unused locals and parameters. Pure configuration-message logic stays
separate from JSX so its Node tests need no browser or JSX loader.

`npm run dev` starts Vite, which proxies `/api` to the local API; Compose serves the built UI
through nginx instead. The GitHub Actions workflow runs backend lint, unit and integration
tests and the offline eval, and frontend tests, typecheck and build.

| Directory | Contents |
|---|---|
| `backend/src/insights/` | API, source, sync, database, analytics, snapshot and narrative modules |
| `backend/migrations/` | Consolidated Alembic initial schema; earlier databases require reset/resync |
| `backend/tests/` | Unit, integration, fixture and golden checks |
| `backend/eval/` | Synthetic generator, scenarios and evaluation runner |
| `frontend/` | React/TypeScript UI, shared abortable requests and formatting, Vite config and nginx image |
| `docs/` | This technical reference and the architecture diagram |

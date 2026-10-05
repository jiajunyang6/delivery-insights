# Technical reference

Detailed behavior moved out of the README. Start with the [README](../README.md) and [NOTES](../NOTES.md).
Current contracts are in code and this reference; [the consolidated plan](PLAN.md) retains historical design input. Implementation decisions are in [DECISIONS.md](DECISIONS.md).

## The insight and why this metric

The core metric is **where PR time goes**: the post-ready waiting time of merged PRs, split
into reviewer, author and merge waiting, and compared with the previous period.
A cited narrative explains a slowdown when the evidence supports one, and otherwise says where
PR time goes now.

| View | Question | Decision it supports |
|---|---|---|
| Time ledger | Who or what is the post-ready wait for? | Review capacity, author follow-up or merge policy |
| Narrative | Did delivery slow down, and which supported cause fits? | Where to investigate first |

The narrative draws on cycle time, first-review wait, review rounds, the review queue and
review concentration, PR size, the weekly series and an accounting
attribution of the added hours. They appear on the page only as cited evidence. Relative
changes use unrounded values, so recomputing them from displayed values can differ slightly.
These are PR-flow signals, not deployment lead time, DORA change failure rate, individual
productivity scores or causal proof. Workflow and timestamp changes affect them.

## How it works

![Delivery Insights data flow and narrative generation](diagrams/how-it-works.svg)

1. **Sync:** staged backfill, overlapping incremental windows and open-PR sweeps write idempotent batches. Coverage and success checkpoints advance after phase completion; the resumable backfill cursor is saved with each batch.
2. **Derive:** an event-driven state machine creates non-overlapping intervals and per-PR facts. A PR is located by its matching labels, otherwise by its most-touched directories. Version changes rederive stored PRs and enqueue snapshot precomputation.
3. **Snapshot:** cached or persisted snapshots are reused. On a miss, a read-only repeatable-read transaction loads a consistent dataset for pure analytics, deterministic bootstrap comparisons and canonical JSON; the result is persisted in a separate write transaction. Parameters, versions and watermarks set identity.
4. **Narrative:** code selects evidence and scores hypotheses. Bedrock supplies wording; local code validates the LLM reply and requests at most one repair. A deterministic template is used when the LLM is disabled, busy, unavailable, times out or remains invalid. The validator and template run inside the API process; the template does not pass through the LLM validator at runtime.

| Component | Responsibility |
|---|---|
| `api` | Local-data queries, snapshot/narrative persistence |
| `worker` | GitHub ingestion, derivation, precomputation, retention |
| `postgres` | Source data, facts, intervals, immutable snapshots and narratives |
| `redis` | arq jobs, locks, response caches and fixed-window limits |
| `web` | React dashboard and nginx same-origin proxy |
| `migrate` | One-shot Alembic schema upgrade before API/worker startup |

Analytics has no database or HTTP imports. The I/O loader lives in `db/dataset.py`.
The API process does not import source adapters or call GitHub on requests.
It may call Bedrock for an uncached narrative; heavy analytics and boto3 run in threads.

## API

OpenAPI is available at `/openapi.json` and `/docs`; the historical design is
[API contract chapter](PLAN.md#plan-06). Dates and timestamps use UTC.
`from` and `to` are inclusive dates; comparison uses the preceding equal-length period. `as_of` is capped by the least recent repository sync watermark.

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/insights/delivery` | Insight snapshot for one tracked repository and period, or `202` Pending |
| GET | `/v1/snapshots/{snapshot_id}/narrative` | English narrative for a retained snapshot |
| GET | `/v1/repos` | Tracked repositories, sync status, date limits and configuration health (`setup`) |
| GET | `/healthz` | Process liveness |
| GET | `/readyz` | Postgres and Redis readiness |

`repo=` names exactly one tracked repository. Unknown parameters are ignored. Invalid input
returns `422`; untracked repos return `403`.
Errors use RFC 9457 `application/problem+json` with `request_id` and sanitized detail.

Once sync has produced a `200` response:

```bash
API=http://localhost:8000
TO=$(python3 -c 'import datetime as d; print(d.datetime.now(d.timezone.utc).date())')
FROM=$(python3 -c 'import datetime as d; print(d.datetime.now(d.timezone.utc).date()-d.timedelta(days=29))')
curl -sD /tmp/di-headers "$API/v1/insights/delivery?repo=bevyengine/bevy&from=$FROM&to=$TO" -o /tmp/di-snapshot.json
SID=$(python3 -c 'import json; print(json.load(open("/tmp/di-snapshot.json"))["snapshot_id"])')
ETAG=$(python3 -c 'from pathlib import Path; print(next(s.split(":",1)[1].strip() for s in Path("/tmp/di-headers").read_text().splitlines() if s.lower().startswith("etag:")))')
curl -i -H "If-None-Match: $ETAG" "$API/v1/insights/delivery?repo=bevyengine/bevy&from=$FROM&to=$TO"
curl -s "$API/v1/snapshots/$SID/narrative"
```

The conditional request returns `304` with no body. Insights use `private, no-cache`, so
browsers revalidate with the ETag and see new coverage as soon as a sync lands. Successful narratives and
disabled-LLM templates have `private, max-age=3600`; failure fallbacks use `no-store`.
Internal Redis TTLs are separate from HTTP cache directives.

A `202` is a Pending object, not a snapshot. Respect `Retry-After`; inspect each repo's
`reason` and `job` (status and phase). The UI polls for at most five minutes, then asks for a
later refresh.

## Snapshot contract

The strict `api/schemas.py` models are the current field contract (`extra="forbid"`). The
snapshot holds the time ledger shown on the dashboard and every metric the narrative evidence
catalog reads, and nothing else.

| Block | Contents | Consumers |
|---|---|---|
| `period`, `as_of`, `repos`, `meta` | Dates, comparison window, observation cutoff, versions, merged sample | Dashboard, evidence pack |
| `time_ledger` | Reviewer/author/merge PR-hours and shares, current and previous | Where PR time goes, E18, E19, E21 |
| `efficiency` | Cycle p50, merged PRs, first-review p50, review rounds, post-review commits, review concentration, PR size p50, large-PR share | E1, E3, E8, E9, E15, E24, E30, E31 |
| `bottleneck_analysis` | Review-queue imbalance weeks, location first-review ratios and reviewer wait | E22, location items |
| `drivers`, `trend` | Slowest-decile size ratio; attribution of added hours by state, location and large PRs | E37, E42, E48, location items |
| `series` | Weekly current and previous values | Effect size and persistence |

Analytics 1.6.0 removed the outputs that only the former dashboard sections or the removed
quality hypothesis consumed: the headline, findings and what-if estimates, at-risk PRs, waste
and revert chains, the guardrail, merge blockers, review load, predictability, Kaplan–Meier
survival, the other drivers and the unused efficiency metrics and signals. Analytics 1.7.0
stops deriving the per-PR facts, links and ownership counts those outputs needed. Every
retained value is unchanged; in the golden output only the snapshot ID and analytics version
differ.

Sampling uses the frozen seed version and the original canonical structure. The retired
`ci_source` key is reconstructed only inside sampling identity; the internal profile
replays the historical GitHub default or the direct/synthetic default. The CI setting
is absent from configuration and API output. Existing GitHub-default and no-CI inputs
keep their bootstrap draws. Removing observed CI intervals can change real-repository
reviewer waiting shares, since timelines are now driven by PR events alone. Old snapshots
and narrative caches stay isolated by version; workers rederive stored PRs in the background.

## Narrative, confidence and evidence chain

Confidence is a **deterministic evidence-strength score**, not a probability.
It has not been calibrated against real repository outcomes or hand-labeled causes.
Correlations and queue pressure are reasons to investigate, not proof.

The model sees structured numbers, evidence IDs and sanitized repository/location names.
It never sees PR titles, bodies, comments or user names. Code supplies evidence references,
sample counts, changes and eligible hypotheses from this library:

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
Invalid output is repaired once using the previous output and exact validation failures.
Timeouts, errors or a second invalid answer return a deterministic template.
Inspect `meta.generated_by`, `validation`, `attempts`, `fallback_reason` and `violations`.
In the UI, citation buttons scroll to and highlight the corresponding evidence entry.

## Configuration

Copy `.env.example`; never commit `.env`. Full environment defaults are in `backend/src/insights/config.py`.

| Variable | Default | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | empty | GitHub collection; missing token is reported explicitly |
| `AWS_BEARER_TOKEN_BEDROCK` | empty | Required for LLM-generated narratives; without it, only deterministic templates are available |
| `AWS_REGION` | `us-west-2` | Bedrock client region |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-6` | Converse model/inference profile |
| `TRACKED_REPOS` | `bevyengine/bevy` | Comma-separated repository allowlist |
| `LOCATION_DIMENSION` | `label:area-` | Label grouping with directory fallback, or directory grouping |
| `BACKFILL_DAYS` | `120` | Final backfill stage, between 30 and 365 |
| `SYNC_INTERVAL_MINUTES` | `15` | Incremental cadence; must divide 60 |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed browser origins |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per-client-IP fixed-window limit on `/v1` |
| `DATABASE_URL` | Compose Postgres URL | Async SQLAlchemy database connection |
| `REDIS_URL` | `redis://redis:6379/0` | Cache, job queue and locks |

Create the GitHub token under Settings → Developer settings → Personal access tokens
→ Fine-grained tokens. Choose Public repositories, an expiration and no extra permissions.
The current implementation targets public repositories; private-repo authorization is not verified.
Model availability and Bedrock access/billing must be verified in your AWS account.

## Operations

`/v1/repos` also returns `setup`: whether `GITHUB_TOKEN` and `AWS_BEARER_TOKEN_BEDROCK` are set (never their values), sync statuses that point to configuration (`missing_token`, `auth_error`, `not_found`), the Bedrock region and model ID, and the latest Bedrock error code, cleared after the next successful call and ignored once `BEDROCK_MODEL_ID` or `AWS_REGION` changes. A connection error (`EndpointConnectionError`) means the endpoint was not reached, so the model ID was not checked; an invalid model ID returns `ValidationException`. The API checks these settings once at startup with a one-token Bedrock request, because cached narratives make no Bedrock call and would otherwise hide a bad key until a new snapshot needs wording. The dashboard turns these into a Configuration notice that names the `.env` variable to fix.

Use `/healthz` for liveness and `/readyz` for dependency readiness. Read `/v1/repos`
before judging missing data. Snapshots/narratives are retained for seven days and sync-job
records for thirty days; source PR records are retained until the database is reset.
Worker housekeeping expires retained data and precomputes 7-, 30- and 60-day reports.

```bash
curl -s http://localhost:8000/readyz
curl -s http://localhost:8000/v1/repos
docker compose logs --tail=50 api worker
```

The worker syncs on a schedule (`SYNC_INTERVAL_MINUTES`); there is no manual sync endpoint.
Rate limiting returns `429` with `Retry-After`.
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

`docker compose down` stops the stack while preserving Postgres data.
`docker compose down -v` intentionally deletes the local database volume; use only for reset.
The unreleased storage schema is consolidated into `0001_initial`. Earlier three-migration
databases require that confirmed reset and a new sync; applying this revision in place is
unsupported. Raw GitHub node IDs and actor types remain in the query for timeline paging and
bot detection. The schema stores only the PR fields and facts that analytics reads: no PR
bodies, head branches, merge commits, author associations, ownership rules or link fields.
Only the `ix_intervals_pr` index was removed, because `UNIQUE(pr_id, seq)` covers its prefix.
Other indexes remain; no performance claim is made without representative EXPLAIN measurements.
There is no authenticated administrative UI or backup orchestration in this demo.

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

```bash
make lint
make test-unit
make test
make eval-offline
# Reads AWS_BEARER_TOKEN_BEDROCK from root .env:
make eval
```

Current checks: 316 backend tests (63 integration), 15 frontend tests,
Ruff/strict mypy, typecheck/build and all eight offline narrative gates pass. Browser
verification uses synthetic data on the newly built UI, including 7/30/60 days, a custom
period, pending, configuration notice, narrative and the three-state ledger. After the user's
database rebuild, the Bevy worker completed the 120-day backfill and precomputed the
7/30/60-day reports. The current real Bedrock run passes all eight gates: first validity
14/15, numeric/citation/hedge consistency 14/14 each and fallback 1/15. The golden
comparison removes only CI fields and changes version/identity; retained values match.

Historical scope-reduction checks (before CI removal):

| Check | Observed result |
|---|---|
| Ruff check/format and strict mypy | Pass |
| Backend suite | 326 passed: 261 unit and 65 integration tests |
| Frontend tests, typecheck and build | 15 tests passed; typecheck/build pass with Node 24 |
| Scope-reduction equivalence | Every retained snapshot value in the golden output matches the pre-reduction golden (290 values); only the snapshot ID and analytics version differ |
| Live rebuild | After a confirmed `down -v`, Bevy synced from empty: the 120-day backfill finished in 293 s with status `ok`; the pending panel showed `backfill:7d` progress first |
| Live browser check | 7, 30 and 60 days: the 7-day `no_slowdown` and 30-day `no_comparison` LLM narratives passed validation first time; the 60-day narrative fell back to the template on a low-band wording violation. No console errors |

Earlier acceptance measured 3,541 PRs with no invariant violations, three matching PR pages, 533 merged PRs with ledger rounding error 0.0000004833, cold compute 1,376.95 ms and warm HTTP p95 32.32 ms.
Those analytics 1.2/1.3 measurements and npm ci/audit checks were not repeated here. They are local measurements, not production load evidence.
The original 90-day performance gate remains excluded; three upstream deprecation warnings remain.

The current harness runs three planted scenarios (review capacity, PR size growth and
no signal) with five seeds: 15 English narratives. CI slowdown is retired. Current offline
and real Bedrock results are in [EVALUATION.md](EVALUATION.md). The real run still falls
back on pr_size_growth seed 303 with `V8:invalid_downgrade`; its low-band candidate does
not demonstrate successful real-model low-band wording.
The following offline and Bedrock results are historical runs with CI analysis enabled.
Numeric/citation/hedge denominators include final LLM outputs, excluding fallback.

| Metric | Offline (v11) | Real Bedrock (v11) | Required |
|---|---|---|---|
| First-attempt validity | 20/20 (1.00) | 19/20 (0.95) | ≥ 0.90 |
| Numeric / citation / hedge consistency | 20/20 each | 19/19 each | 1.00 each |
| Root-cause hit rate | 15/15 (1.00) | 15/15 (1.00) | ≥ 0.80 |
| No-signal abstention | 5/5 (1.00) | 5/5 (1.00) | ≥ 0.80 |
| High-confidence precision | 14/14 (1.00) | 14/14 (1.00) | ≥ 0.80 |
| Fallback rate | 0/20 (0.00) | 1/20 (0.05) | ≤ 0.10 |

Both v11 suites pass all gates. The real fallback (pr_size_growth seed 303) is a low-band
wording failure on a secondary hypothesis, also seen in live 60-day Bevy narratives.
Medium/low precision is undefined. Failed v1/v2 and English v4/v5 trials remain
recorded, together with rejected stage-8 candidates A/B. Earlier rebuilt-API English-only real-manager HTTP smoke is historical; the refactor UI check used synthetic fixtures.
This small synthetic suite was used during prompt development; it is not a held-out
benchmark or real-world causal calibration. Missing-key evaluation exits 2.

## Trade-offs and limitations

### Key trade-offs

| Decision | Choice and cost | Follow-up |
|---|---|---|
| Time basis | UTC wall-clock, including nights/weekends | Add team calendars |
| Locations | Current labels → directories; historical labels unavailable | Choose a label prefix that matches the repository's area labels |
| Scope | Default-branch flow; bots/backports excluded | Separate release-branch view |
| Freshness | Background sync, default 15 minutes | Webhooks if lower latency is needed |
| Sources | Whitelisted GitHub repos only | Extend `SourceAdapter` with shared normalized records |
| Snapshot compute | Concurrent cold requests can duplicate deterministic work | Add single-flight only if measured necessary |
| Commit times | Committer timestamps approximate push/revision time | Collect push events |
| Reopened PRs | Closed intervals excluded from ledger; elapsed milestones retain them | Review prevalence before changing duration semantics |
| Confidence | Evidence score tested only on synthetic scenarios | Replay history and calibrate with human labels |
| Size scenario | Synthetic duration scales with square root of planted size multiplier | Validate this assumption with real observations |

Implementation-specific decisions and their reasons are recorded in [DECISIONS.md](DECISIONS.md).

### Things deliberately not done

No individual productivity rankings, request-time GitHub fetching, arbitrary repo/org discovery,
LLM arithmetic, self-assigned LLM confidence or raw-text prompts. No authentication, deployment
integration, webhook ingestion, second source adapter implementation or business-hours mode.
Revert, reland and supersession links are not derived.
Real-history backtesting, human calibration, AI-authorship analysis, stacked-PR dependency
waiting, cumulative-flow charts and release/deployment timing remain outside this version.

### Beyond the brief

Implemented: deterministic evidence scoring and validation, repair/template fallback, immutable
snapshots and ETags, staged backfill/open sweeps,
an English narrative with two scored hypotheses, offline eval, React dashboard,
Docker and automated verification config.

### Known limitations

- PRs that have been open without human activity during the selected period are not listed;
  extend the date range to include them.
- PRs opened before the selected period and merged by a bot with no human activity in
  the period are not counted in throughput.

GitHub omits design discussions, offline coordination and deployments. Rewritten or rebased
commit timestamps distort coding time. Current labels are not historical ownership.
Small samples suppress p50 below 20 and rates below 30 cases/5 events.
Driver/location measures have their own documented gates. Insufficient values remain null.
Confidence still needs historical replay and human labels.
Skipped PRs and anomalous timelines can affect metrics; inspect sync job skipped_prs and invariant_violations counters and their structured warnings before relying on a report.
Net review-demand share compares arrivals with first reviews, including service of earlier demand; it can be negative and is not an individually tracked unreviewed-PR fraction.
The plan-selected Recharts 2 branch is deprecated; migration to v3 is a maintenance follow-up.
The supplied golden fixture still needs a person's numeric review; browser checks here were
performed by the coding agent, not signed off by a human. Remote CI has not been run.

## Development

Use Python 3.12 and uv for backend development, Node 24 for the frontend.
Lockfiles are committed; use frozen installs for reproducibility.

```bash
cd backend
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -m "not integration"
uv run pytest
uv run python -m insights_eval.run --llm stub
cd ../frontend
npm ci
npm test
npm run typecheck
npm run build
npm run dev
```

The delivery snapshot and narrative routes, including HTTP reply conversion, live in
`api/routes/insights.py`. The PR-size driver shares `analytics/efficiency.py`; the stable
numbered catalog shares `narrative/evidence.py`; `sources/__init__.py` exports the source
protocol. Health and repository routes remain separate. Analytics input records contain
only consumed fields; the unused current-day marker is absent. Bootstrap supports the
retained median and mean statistics. The loader does not select PR titles, URLs, authors
or draft flags for snapshots. Raw source records and persistence retain their existing fields.

Frontend `api.ts` owns fetch/polling and the `useAbortable` effect, which cancels superseded
requests and requests still active on unmount. `format.ts` owns display and UTC date helpers.
TypeScript rejects unused locals and parameters. Pure configuration-message logic stays
separate from JSX so its Node tests need no browser or JSX loader.

Cleanup verification: OpenAPI and 15 synthetic snapshots, evidence packs and template
narratives match their pre-cleanup values exactly. All 57 backend modules import in separate
fresh interpreters. The golden snapshot and analytics, sampling and prompt versions are
unchanged. Full backend/frontend checks and all eight offline narrative gates pass.

Vite proxies `/api` to the local API. Compose serves the built UI through nginx instead.
Make targets: `up`, `down`, `logs`, `lint`, `fmt`, `test-unit`, `test`, `eval-offline`, `eval`.
The GitHub Actions workflow applies backend lint/tests/eval and frontend typecheck/build.

| Directory | Contents |
|---|---|
| `backend/src/insights/` | API, source, sync, database, analytics and narrative modules |
| `backend/migrations/` | Consolidated Alembic initial schema; earlier databases require reset/resync |
| `backend/tests/` | Unit, integration, fixture and golden checks |
| `backend/eval/` | Synthetic generator, scenarios and evaluation runner |
| `frontend/` | React/TypeScript UI, shared abortable requests and formatting, Vite config and nginx image |
| `backend/src/insights/snapshots/` | Shared orchestration, readiness, caching and domain errors |
| `backend/src/insights/sync/queue.py` | Shared job lifecycle, locks and queue helpers |
| `PLAN.md` | Consolidated historical design input |
| `docs/` | Decisions, acceptance evidence and evaluation records in Markdown |

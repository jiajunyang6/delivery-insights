# 00 Overview

This is the implementation plan. User-approved scope and tooling changes are recorded in `docs/DECISIONS.md`; the
current behavior and verification status are described in README and `docs/ACCEPTANCE.md`.

## 1. Product

**Delivery Insights** synchronizes GitHub PR collaboration data to answer four questions for engineering managers: where work is stuck, why it is slow, where the risks are, and where to intervene.

The core metric is **PR delivery cycle time and who the PR is waiting on**: elapsed time from starting a change to merging it, including waiting on reviewers, author updates, CI, or merge. Explain the choice in English in the README:

- It supports management decisions: splitting the cycle by waiting state and location (area / directory) connects each bottleneck to a concrete action.
- GitHub provides rich signals: PR timelines record author, reviewer, CI, and merge actions.
- It uses familiar industry measures: cycle time corresponds to DORA's Lead Time for Changes; revert rate approximates change failure rate.
- It discourages gaming: reducing waiting requires process improvements; revert rate guards against speeding up delivery by weakening review.

The output has two parts:

- **Team efficiency (outcomes)**: delivery speed, stability, wasted effort, and changes from the previous period.
- **Bottleneck analysis (causes)**: where time goes → where work is stuck → why → impact and priorities.

The headline joins both parts: efficiency change → main bottleneck → recommendation → expected benefit.

Demo repository: **`dotnet/runtime`** (large, active, with ownership areas identified by `area-*` labels).

## 2. Scope

### P0 (required)

1. GitHub synchronization: staged GraphQL backfill (7 → 30 → 180 days) and incremental synchronization into Postgres (arq worker).
2. Derivation: per-PR waiting-state intervals, stage durations, and facts; closed-PR classification; revert / reland / supersession chains; bot and backport detection.
3. Efficiency metrics: effective throughput, cycle time (p50/p90), share merged within N days, waiting share (of the full cycle), waste rate, rework rate, review-load concentration, and revert guardrail.
4. Bottlenecks: time ledger, review-queue inflow/outflow, location analysis (default `area-*` labels, directory fallback), merge-block decomposition, cumulative waiting ranking (Pareto), what-if estimates, at-risk PRs (historical p85), bottleneck shifts, findings rule engine, and headline.
5. Endpoint 1: snapshots, Redis caching, ETag/304, problem+json, asynchronous 202 flow, PR detail endpoint, repository and sync-job endpoints, and health checks.
6. Endpoint 2: evidence pack, deterministic confidence, Bedrock calls, validator, retries, template fallback, and caching.
7. Core tests, one-command Docker Compose startup, and README (60-second quickstart).

### P1 (extras, after P0)

1. Eval harness(`make eval`).
2. Single-page React frontend.
3. CODEOWNERS and `docs/area-owners.md` parsing, with owner counts by location.
4. CI waiting: queue/run durations and flaky reruns from GitHub Actions runs (dotnet/runtime's main CI uses Azure Pipelines and appears as GitHub check runs, which this version does not collect; incomplete CI data caps CI-hypothesis confidence).
5. Drivers: assignment, review-round costs, concurrent author PRs, submission timing, and characteristics of the slowest 10%.
6. Survival analysis (Kaplan–Meier) and predictability metrics.
7. Director and manager narrative variants (implemented with M7).

### P2 (excluded; describe in README "Not done")

Merge-to-release waiting, effects of AI-authored PRs on review, dependency waiting (stacked PRs, blocked labels), cumulative flow diagrams, business-hours calculations, a second source adapter, an expanded hypothesis library, real-time webhooks, and API authentication.

**Deferred** (mentioned in the design but excluded here; record each in `docs/DECISIONS.md` and README "Not done"): chain-level delivery time for supersession and revert / reland chains (links only in this version, `05` §4.5); real-history threshold backtesting and confidence-band calibration (`08` §5).

### Non-goals

- Individual productivity metrics, individual leaderboards, or cross-team rankings.
- Calling GitHub on the request path.
- Letting the LLM calculate numbers.

## 3. Architecture

```
                 ┌──────────────┐   GraphQL + REST (read-only token)
 GitHub API ◀────┤ worker (arq) │  backfill / incremental / open-PR sweep
                 └──────┬───────┘
                        │ upsert raw PRs, events, files (+ CI runs, ownership: P1)
                        ▼
                 ┌──────────────┐  derive (pure functions):
                 │  Postgres    │  pr_intervals (who-are-we-waiting-on), pr_facts
                 └──────┬───────┘
                        │ read only
  Browser ──▶ web ──▶ ┌─┴────────────┐     ┌──────────┐
  (React)   (nginx)   │ api (FastAPI)├────▶│  Redis   │ snapshot cache, locks,
  curl ─────────────▶ └─┬────────────┘     └──────────┘ rate limits, arq queue
                        │ snapshot ──▶ evidence pack ──▶ Bedrock (Claude Sonnet 4.6)
                        │                                   │
                        └──────────── validator ◀───────────┘ (retry once, else template)
```

Key points:

- The API **only reads** Postgres and Redis; all GitHub calls run in the worker.
- PR state intervals and facts are derived once during synchronization; requests only aggregate them for fast responses.
- Snapshots are immutable: parameters and data versions determine IDs. Narratives attach to snapshots so text and numbers refer to the same data.

## 4. Technology stack (minimum versions; exact versions locked by uv)

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| API | FastAPI ≥ 0.115, uvicorn[standard] ≥ 0.30, Pydantic ≥ 2.8, pydantic-settings ≥ 2.4 |
| Storage | Postgres 16, SQLAlchemy[asyncio] ≥ 2.0.30, asyncpg ≥ 0.29, Alembic ≥ 1.13 |
| Cache and queue | Redis 7, redis-py ≥ 5.0 (`redis.asyncio`), arq ≥ 0.26 |
| Upstream client | httpx ≥ 0.27 |
| LLM | boto3 ≥ 1.40 (`bedrock-runtime` Converse API, Bedrock API key authentication) |
| Computation | numpy ≥ 2.0 |
| Other | orjson ≥ 3.10, structlog ≥ 24.1, pathspec ≥ 0.12 (P1, CODEOWNERS matching) |
| Testing and quality | pytest ≥ 8, pytest-asyncio ≥ 0.23, respx ≥ 0.21, testcontainers[postgres,redis] ≥ 4.4, ruff ≥ 0.5, mypy ≥ 1.10 |
| Frontend (P1) | Node 20, React 18, Vite 5, TypeScript 5, Recharts 2 |
| Deployment | Docker, Docker Compose v2, nginx (frontend static files and `/api` reverse proxy) |

Do not use pandas (numpy is sufficient and keeps the image smaller).

## 5. Repository layout

```
delivery-insights/
├── AGENTS.md  README.md  Makefile
├── docker-compose.yml  .env.example  .gitignore  .dockerignore
├── .github/workflows/ci.yml
├── scripts/smoke.sh               # End-to-end smoke test (02 §7)
├── docs/
│   ├── plan/                      # This plan
│   └── DECISIONS.md               # Deviations recorded during implementation
├── backend/
│   ├── pyproject.toml  uv.lock  Dockerfile  alembic.ini
│   ├── migrations/                # Alembic (async template)
│   ├── src/insights/
│   │   ├── __init__.py            # __version__
│   │   ├── main.py                # create_app()
│   │   ├── config.py              # Settings
│   │   ├── logging.py             # structlog JSON logging
│   │   ├── db/
│   │   │   ├── engine.py          # async engine / session factory
│   │   │   └── models.py          # SQLAlchemy models
│   │   ├── redis.py               # Redis connections and key builders
│   │   ├── domain.py              # Source-independent domain dataclasses
│   │   ├── snapshot_service.py    # Shared readiness, cache, computation, persistence (API/worker, 06 §5.1)
│   │   ├── sources/
│   │   │   ├── base.py            # SourceAdapter Protocol
│   │   │   └── github/
│   │   │       ├── client.py      # httpx client: GraphQL, REST, rate limits, ETag
│   │   │       ├── queries.py     # GraphQL query text
│   │   │       ├── normalize.py   # GitHub JSON → domain dataclasses
│   │   │       ├── adapter.py     # GitHubAdapter
│   │   │       ├── ownership.py   # P1: CODEOWNERS and area-owners parsing
│   │   │       └── smoke.py       # Smoke command (credentials required)
│   │   ├── sync/
│   │   │   ├── worker.py          # arq WorkerSettings
│   │   │   ├── jobs.py            # arq job functions
│   │   │   ├── queue.py           # Shared enqueue_sync; no sources imports (04 §6.8)
│   │   │   ├── store.py           # Upserts and reads
│   │   │   ├── invariants.py      # Invariant CLI (reads DB, calls timeline.check_invariants)
│   │   │   └── derive.py          # Derivation orchestration using analytics pure functions
│   │   ├── analytics/
│   │   │   ├── thresholds.py      # All threshold constants
│   │   │   ├── timeline.py        # State machine → intervals (pure function)
│   │   │   ├── facts.py           # Per-PR facts (pure function)
│   │   │   ├── classify.py        # Bots, backports, closed PRs, revert/reland/supersession (pure functions)
│   │   │   ├── stats.py           # Quantiles, bootstrap, KM (pure functions)
│   │   │   ├── dataset.py         # Load data needed for a period from the database
│   │   │   ├── efficiency.py
│   │   │   ├── bottlenecks.py
│   │   │   ├── findings.py
│   │   │   ├── drivers.py         # P1
│   │   │   ├── ci.py              # P1
│   │   │   ├── snapshot.py        # Snapshot/headline assembly, canonical JSON, hashing
│   │   │   └── rows.py            # PR detail rows (/v1/insights/delivery/prs)
│   │   ├── narrative/
│   │   │   ├── evidence.py        # Evidence pack
│   │   │   ├── hypotheses.py      # Hypothesis library and scoring
│   │   │   ├── prompt.py          # system prompt, tool schema
│   │   │   ├── llm.py             # LLMClient Protocol, BedrockClient, FakeLLMClient
│   │   │   ├── validator.py
│   │   │   ├── template.py        # Template narrative
│   │   │   └── service.py         # Generation, validation, retries, fallback, caching
│   │   └── api/
│   │       ├── deps.py  errors.py  middleware.py  params.py
│   │       ├── schemas.py         # Pydantic response models (matching 06-api.md)
│   │       ├── caching.py         # ETag utilities
│   │       └── routes/  health.py  insights.py  snapshots.py  repos.py  sync_jobs.py
│   ├── eval/insights_eval/        # generator.py, pipeline.py, scenarios.py(M5);stub_llm.py, metrics.py, run.py(M10)
│   └── tests/  unit/  integration/  fixtures/  golden/
└── frontend/                      # P1
    ├── package.json  vite.config.ts  tsconfig.json  index.html
    ├── Dockerfile  nginx.conf
    └── src/
```

Package `eval/` separately as `insights_eval` under `backend/eval/insights_eval/`, alongside `insights` in `pyproject.toml` (see 02).

## 6. Terminology

| Term | Meaning |
|---|---|
| ready_at | Time the PR becomes reviewable: `created_at` if opened non-draft; otherwise the first `ReadyForReviewEvent` |
| Stage | coding (first commit → ready), pickup (ready → first human review), review (first review → approval), merge (approval → merge) |
| Ledger state | Each post-ready interval belongs to exactly one of `waiting_reviewer`, `waiting_author`, `waiting_ci`, `waiting_merge`; pre-ready time is `coding`; temporary closure before reopening is `closed` and is not waiting |
| Human review | Review by a non-author, non-bot account with APPROVED / CHANGES_REQUESTED / COMMENTED state |
| Location | Bottleneck-location value, such as `area-System.Net.Http` or `dir:src/coreclr` |
| Snapshot | Complete immutable insight for repositories, period, and data version |
| data_version | Per-repository data version, incremented when synchronization changes data |
| as_of | Snapshot observation time: `min(midnight after to, minimum repository last_synced_at)` (`05` §6.1); `period.complete` indicates full observation of the requested period |
| Coverage | Earliest synchronized repository time, `covered_since` |
| Evidence pack | Structured evidence generated from a snapshot and passed to the LLM |
| Hypothesis library | Predefined root-cause hypotheses, each requiring an efficiency symptom and a bottleneck mechanism |

## 7. Global conventions

- **Time**: timezone-aware UTC `datetime`. Day-based `[from, to]` maps to `[from 00:00Z, to+1 00:00Z)`. Durations are hours (`float`), rounded to 2 decimals in API output.
- **Time basis**: UTC wall-clock time, including weekends and holidays (document in README).
- **Population**: PRs targeting the default branch only; backports, bot-authored PRs, and drafts never made ready are excluded from flow metrics and counted in `meta.excluded`.
- **Team-level reporting**: individuals appear only in `review_load.distribution` and the at-risk PR `author` field.
- **Determinism**: bootstrap seeds derive from the snapshot parameter hash; all lists have explicit sort orders (see individual specifications).
- **Injectable time**: domain code does not call `datetime.now()` directly. The API uses `insights.api.deps.get_now`; worker jobs and services receive `now` so tests and synthetic data can fix "today".
- **Versions**: `insights.analytics.ANALYTICS_VERSION = "1.0.0"`, `thresholds.THRESHOLDS_VERSION = "1.0.0"`, `narrative.prompt.PROMPT_VERSION = "v6"`. Increment the relevant version when algorithms, thresholds, or prompts change to avoid old cache hits.

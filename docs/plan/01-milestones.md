# 01 Milestones (execution sequence)

Execute in order. Each milestone specifies its goal, tasks, required tests, definition of done (DoD), and notes. Run every DoD command and require it to pass. Skip steps marked "credentials required" when the corresponding environment variable is absent, and record them in the final report.

| Milestone | Content | Priority |
|---|---|---|
| M0 | Scaffold, configuration, logging, health checks, containers | P0 |
| M1 | Data model and migrations | P0 |
| M2 | GitHub client and normalization | P0 |
| M3 | Synchronization worker | P0 |
| M4 | Derivation: state machine, PR facts, classification, links | P0 |
| M5 | Analytics and snapshots | P0 |
| M6 | API: Endpoint 1 and supporting endpoints | P0 |
| M7 | Narrative: Endpoint 2 (director / manager variants) | P0 (variant distinction is P1, implemented together) |
| M8 | CI waiting and ownership parsing | P1 |
| M9 | Drivers, survival analysis, predictability | P1 |
| M10 | Eval harness | P1 |
| M11 | Frontend | P1 |
| M12 | Finalization: README, security/performance checks, acceptance | P0 |

---

## M0 Scaffold and engineering foundation

**Goal**: a runnable empty service with working quality gates and containers.

**Tasks**

1. Create directories per `00-overview.md` §5. Write `backend/pyproject.toml` per `02-config-and-infra.md` §2 and run `cd backend && uv lock`.
2. `insights/config.py`: implement `Settings` (pydantic-settings) per `02` §1 and cached `get_settings()` (`functools.lru_cache`).
3. `insights/logging.py`: configure structlog JSON logging per `02` §4.
4. `insights/db/engine.py`: create an async engine and `async_sessionmaker`; `insights/redis.py`: create `redis.asyncio.Redis`. Create both in FastAPI lifespan, dispose on shutdown, and inject into routes. `insights/api/deps.py` provides `get_settings`, `get_session`, `get_redis`, `get_now` (returns `datetime.now(UTC)`, overridden in tests; `00` §7).
5. `insights/api/errors.py`: problem+json exception hierarchy and handlers (`06-api.md` §2.3); `insights/api/middleware.py`: request ID and access logs (rate limiting added in M6).
6. `insights/api/routes/health.py`: `GET /healthz` (no dependency checks, returns `{"status": "ok"}`), `GET /readyz` (checks `SELECT 1` and Redis `PING`; either failure returns 503 problem+json).
7. `insights/main.py`: assemble `create_app()`; expose `app = create_app()` for uvicorn.
8. Write `backend/Dockerfile`, `docker-compose.yml` (initially `postgres`, `redis`, `api` only), `Makefile`, `.env.example`, `.gitignore`, `.dockerignore`, `.github/workflows/ci.yml` per `02` §5–§8.
9. Create `docs/DECISIONS.md` with a title and table headers.

**Tests**: `tests/unit/test_health.py` (override dependencies for database/Redis success and failure), `tests/unit/test_logging.py` (JSON format and fields; `02` §4).

**DoD**

```bash
make lint && make test-unit
cp .env.example .env
docker compose up --build -d
curl -s localhost:8000/healthz                                   # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/readyz   # 200
curl -s localhost:8000/openapi.json | python -c "import sys,json; print(json.load(sys.stdin)['info']['title'])"
docker compose down -v
```

**Note**: `/healthz` and `/readyz` have no `/v1` prefix and are exempt from rate limiting.

---

## M1 Data model and migrations

**Goal**: complete schema and migrations as the foundation for all later milestones.

**Tasks**

1. `insights/db/models.py`: implement all tables per `03-data-model.md` §2, including P1 `workflow_runs` and `ownership_rules` to avoid later schema changes.
2. Run `cd backend && uv run alembic init -t async migrations`; configure `env.py` to read `Settings.database_url`, with `target_metadata = Base.metadata`.
3. Generate `0001_initial`; **inspect each line** against `03` (indexes, uniqueness, `ON DELETE CASCADE`, `server_default`).
4. Implement Redis key builders per `03` §3 in `insights/redis.py`; do not scatter handwritten keys across the code.
5. Add a `migrate` Compose service (`alembic upgrade head`); `api` depends on its successful completion.

**Tests**: `tests/integration/test_migrations.py` (Testcontainers Postgres): verify all tables/key indexes after `upgrade head`; successful `downgrade base`.

**DoD**

```bash
make lint && make test
docker compose up --build -d && docker compose ps -a  # migrate exited (0), api healthy
docker compose exec postgres psql -U insights -d insights -c '\dt'
docker compose down -v
```

---

## M2 GitHub client and normalization

**Goal**: reliably read GitHub PR data and normalize it into source-independent domain objects.

**Tasks**

1. `insights/domain.py`: define domain dataclasses (`frozen=True, slots=True`) per `04-github-sync.md` §2.
2. `insights/sources/base.py`:`SourceAdapter` Protocol(`04` §2.3).
3. `insights/sources/github/queries.py`: copy the GraphQL queries from `04` §3 **verbatim**.
4. `insights/sources/github/client.py`: GraphQL / REST calls, rate-limit waiting, retries, adaptive page sizes, and conditional ETag requests per `04` §4.
5. `insights/sources/github/normalize.py`: convert GraphQL nodes into domain objects per `04` §5, including bot detection, revert commit parsing, and event dedup keys.
6. `insights/sources/github/adapter.py`: implement `SourceAdapter` as `GitHubAdapter`.
7. `insights/sources/github/smoke.py`: CLI smoke test (`python -m insights.sources.github.smoke --repo OWNER/NAME --pages N`); print PR/event counts and remaining quota, never tokens.

**Tests**: `tests/unit/test_github_client.py`, `tests/unit/test_normalize.py` (`10-testing.md` §3). Handwrite response fixtures in `tests/fixtures/github/`, matching `04` §3 queries exactly.

**DoD**

```bash
make lint && make test-unit
# Credentials required:
cd backend && uv run python -m insights.sources.github.smoke --repo dotnet/runtime --pages 1
#   Expected: "prs=25 events=... rate_limit_remaining=...", no GraphQL errors
```

**Note**: if a live call rejects a field, consult GitHub GraphQL documentation, fix the query, and update fixtures and `docs/DECISIONS.md`.

---

## M3 Synchronization worker

**Goal**: staged background backfill, incremental synchronization, and open-PR sweeps with idempotent storage.

**Tasks**

1. `insights/sync/store.py`: transactional per-page upserts of PRs/events/files (`04` §7); detect changes via `content_hash` and increment repository `data_version`. Integrate `derive.derive_prs` and `derive.link_repo` from `04` §6.3/§7 in M4; M3 stores raw data only.
2. `insights/sync/jobs.py`: implement `sync_repo` per `04` §6 (incremental first if `sync_watermark` exists, then resume backfill; 7 → 30 → `BACKFILL_DAYS`, resumable cursors; open-PR sweep after stage one; at each stage completion and successful job end run checkpoint finalization: catch-up incremental → publish `covered_since`/`last_synced_at`, `04` §6.3; link_repo/completeness checks added in M4), scheduled `incremental_sync_all`, `open_pr_sweep`, and startup `reconcile_tracked_repos` (update `repositories.tracked` from `TRACKED_REPOS`, enqueue uncovered repositories). Implement shared `enqueue_sync` in `insights/sync/queue.py` (`04` §6.8) for all synchronization jobs. Add `precompute_snapshots` in M6; do not enqueue it in M3.
3. `insights/sync/worker.py`:arq `WorkerSettings`(`04` §6.1),`max_jobs = 1`.
4. Record each job in `sync_jobs` (queued → running → succeeded / failed, with phase, stats, error summary). Redis locking permits one sync job per repository.
5. Add Compose `worker` service (`arq insights.sync.worker.WorkerSettings`).

**Tests**: `tests/integration/test_sync.py` (Testcontainers + respx; `10` §4).

**DoD**

```bash
make lint && make test
# Credentials required:
docker compose up --build -d
sleep 180
docker compose exec postgres psql -U insights -d insights -c \
  "select full_name, covered_since, data_version, last_sync_status from repositories"
#   Expected: dotnet/runtime covered_since approximately now() - 7 days or earlier, data_version >= 1
docker compose exec postgres psql -U insights -d insights -c "select count(*) from pull_requests"
```

---

## M4 Derivation: state machine, PR facts, classification, links

**Goal**: turn raw events into per-PR waiting intervals and facts. All metrics depend on their correctness; test thoroughly.

**Tasks**

1. `insights/analytics/timeline.py`: pure state machine per `05-analytics.md` §2, returning continuous non-overlapping intervals.
2. `insights/analytics/facts.py`: per-PR facts per `05` §3.
3. `insights/analytics/classify.py`: bots/backports/external contributors, closed-PR classes, revert/reland/supersession links (`05` §4).
4. `insights/sync/derive.py`: recompute changed PRs after each page upsert; replace their `pr_intervals`, upsert `pr_facts` without overwriting linkage fields on conflict (`05` §3), and write the current derivation identity (`insights.analytics.derive_key(...)`, `05` §1). At checkpoint finalization (each backfill stage and sync completion), run repository linking (`05` §4.3–§4.7: pure `classify.link_prs`, I/O `derive.link_repo`) and verify derivation completeness: publish `repositories.derived_key` only when all PRs have the current identity; otherwise enqueue rederivation (`04` §6.3).
5. Derivation identity: at worker startup and every scheduled cycle, enqueue `kind="rederive"` for repositories with non-null `covered_since` whose `repositories.derived_key` differs from the current identity, including null (`04` §6.2 step 3 / §6.4; enqueue even without a token). `rederive_repo` uses `pr_id` keyset pagination, commits each batch, skips current PRs, and publishes `derived_key` plus an incremented `data_version` in one final transaction (`04` §6.5).
6. `insights/sync/invariants.py`: CLI reads DB and calls pure `timeline.check_invariants` (`05` §2.6); keep it in `sync/` to preserve analytics' no-I/O boundary. `python -m insights.sync.invariants --repo OWNER/NAME` prints violation counts and exits nonzero on violations.

**Tests**: `tests/unit/test_timeline.py` (all 22 `05` §2.5 cases plus randomized invariant checks), `test_facts.py`, `test_classify.py` (`10` §3). Integration verifies persisted intervals/facts and invariants; include `10` §4 identity cases marked "M4 onward".

**DoD**

```bash
make lint && make test
# Credentials required (M3 data already synchronized):
docker compose exec api python -m insights.sync.invariants --repo dotnet/runtime   # 0 violations
```

---

## M5 Analytics and snapshots

**Goal**: compute a complete deterministic, reproducible snapshot from `pr_facts` / `pr_intervals`.

**Tasks**

1. `insights/analytics/thresholds.py`: all `05` §1 constants.
2. `insights/analytics/stats.py`: quantiles with sample gates and seeded bootstrap (`05` §5).
3. `insights/analytics/dataset.py`: load immutable in-memory data per period (`05` §6), selecting only required columns.
4. Implement `efficiency.py`, `bottlenecks.py` (including change attribution, `05` §9.12), and `findings.py` per `05` §7–§11.
5. `insights/analytics/snapshot.py`: snapshot/headline assembly, canonical JSON, `snapshot_id` and ETag per `05` §12 / `06-api.md` §4. `insights/analytics/rows.py`: PR details (`05` §17).
6. Implement synthetic generator, pure records → snapshot pipeline, and scenarios in `backend/eval/insights_eval/{generator,pipeline,scenarios}.py` per `08-eval-harness.md` §2–§3; initially used for golden/M7 template tests and reused in M10.

**Tests**: module unit tests (`10` §3, including `test_rows.py`); seed-42 snapshot must exactly match `tests/golden/snapshot_seed42.json` (`UPDATE_GOLDEN=1` regenerates it); repeated builds from identical inputs produce identical bytes.

**DoD**

```bash
make lint && make test
```

**Note**: after first generating the golden file, a **human must spot-check** key values (for example, ledger totals equal PR interval sums) before committing.

---

## M6 API: Endpoint 1 and supporting endpoints

**Goal**: complete curl-accessible REST API with correct HTTP semantics.

**Tasks**

1. `insights/api/params.py`: parse/validate parameters (`06` §3).
2. `insights/api/schemas.py`: Pydantic response models matching `06` §4–§7 exactly.
3. `insights/api/caching.py`: ETags and `If-None-Match` (`06` §2.4).
4. `insights/snapshot_service.py`: resolve repositories → enforce whitelist → check all five readiness conditions and `reason` (`06` §5.1 step 4; return 202/503 without enqueueing) → compute `data_freshness`, `as_of`, `snapshot_id` → Redis/Postgres lookup → on miss compute with `asyncio.to_thread` and persist; log `snapshot_computed` with `duration_ms`, `load_ms`, `compute_ms`, `merged_prs` (`06` §5.1).
5. Routes: `insights.py` (`/v1/insights/delivery`, `/v1/insights/delivery/prs`), `snapshots.py` (`/v1/snapshots/{id}`), `repos.py` (`GET /v1/repos`, `POST /v1/repos/{owner}/{name}/sync` via `enqueue_sync`; lifespan creates arq pool injected via `get_arq`, `06` §5.6), `sync_jobs.py`.
6. Add Redis rate limiting (`06` §2.6); CORS allows only `CORS_ORIGINS`.
7. Integrate worker `precompute_snapshots` (`04` §6.6); enqueue after sync jobs that changed `data_version` (`04` §6.3 step 7).
8. Integrate worker `housekeeping` (`04` §6.7): delete expired snapshots/Redis keys/old `sync_jobs` per `03` §2.12. Reads enforce seven-day logical expiry; Redis hashes retain `created_at`, and cache refill TTL is capped by remaining lifetime.
9. Draft README quickstart and curl examples (completed in M12).

**Tests**: `tests/integration/test_api.py` (`10` §4).

**DoD**

```bash
make lint && make test
# Credentials required: execute every curl example in 06-api.md §8; verify described 200 / 304 / 422 / 403 behavior
```

---

## M7 Narrative: Endpoint 2

**Goal**: the LLM writes narrative only; code controls numbers, confidence, and validation; failures fall back safely.

**Tasks**

1. `insights/narrative/evidence.py`: evidence pack (`07` §2).
2. `insights/narrative/hypotheses.py`: hypothesis library and scoring (`07` §3–§4).
3. `insights/narrative/prompt.py`: system prompt, tool schema, user message (`07` §5), `PROMPT_VERSION = "v1"`.
4. `insights/narrative/llm.py`: `LLMClient` Protocol, `BedrockClient` (boto3 Converse in `asyncio.to_thread`), programmable `FakeLLMClient` for tests (`07` §6).
5. `insights/narrative/validator.py`(`07` §7)、`template.py`(`07` §8)、`service.py`(`07` §9).
6. Route `GET /v1/snapshots/{snapshot_id}/narrative`, supporting `audience=director|manager`, `lang=en|zh` (`06` §6).

**Tests**: `test_evidence.py`, `test_hypotheses.py` (all `07` §4.5 examples: 0.78, counter-evidence 0.63, no mechanism produces no hypothesis, incomplete-data cap 0.5), `test_validator.py`, `test_template.py` (templates must pass validation), `test_llm.py`, `test_narrative_service.py`; endpoint integration with `FakeLLMClient` (`10` §4).

**DoD**

```bash
make lint && make test
# No Bedrock key: narrative endpoint returns 200 with meta.generated_by == "template"
# Bedrock credentials required: last-30-day dotnet/runtime narrative has meta.generated_by == "llm", meta.validation == "passed"
```

**P0 completion**: execute every P0 item in `12-acceptance-checklist.md`; proceed to P1 only after all pass.

---

## M8 (P1) CI waiting and ownership parsing

**Tasks**

1. Client/sync: collect GitHub Actions runs per `04` §9, using daily windows split further above 1,000 results; persist `workflow_runs` and map to PRs.
2. State machine: include CI intervals in `waiting_ci` (`05` §2.4) and rederive affected PRs.
3. `insights/analytics/ci.py`: CI queue/run times, flaky reruns, coverage (`05` §13); populate `bottleneck_analysis.ci` and configure `CI_COMPLETE` (`02` §1). Cap CI hypotheses at 0.5 for coverage below 0.5 or `CI_COMPLETE=false` (`07` §4.3).
4. `insights/sources/github/ownership.py`: parse CODEOWNERS and `docs/area-owners.md` (`04` §10); location fallback label → CODEOWNERS → directory (`05` §4.2); populate `owners_count` (`05` §9.1).

**Tests**: `test_ci.py`, `test_ownership.py`; integration covers run-to-PR mapping.

**DoD**: `make lint && make test`; snapshot includes `bottleneck_analysis.ci` and `time_ledger.ci_coverage`; with credentials, dotnet/runtime area locations have `owners_count`.

---

## M9 (P1) Drivers, survival analysis, predictability

**Tasks**: implement `drivers.py`, `stats.kaplan_meier`, and predictability per `05` §14–§16; populate snapshot `drivers`, `efficiency.survival`, `efficiency.predictability`; add P1 evidence rows (`07` §2.1).

**Tests**: hand-calculated KM example (`10` §3), small driver datasets; update the golden file and explain in the commit message.

**DoD**:`make lint && make test`.

---

## M10(P1)Eval harness

**Tasks**: scenarios, runner, metrics, `StubLLMClient`, reports and comparison per `08-eval-harness.md`; `make eval`, `make eval-offline`.

**DoD**

```bash
make eval-offline        # Exit 0; print per-scenario results
# Bedrock credentials required:
make eval                # Meet 08 §6 gates, exit 0
```

---

## M11 (P1) Frontend

**Tasks**: single-page UI, `frontend/Dockerfile`, `frontend/nginx.conf` per `09-frontend.md`; add Compose `web` (host port 5173).

**DoD**

```bash
cd frontend && npm ci && npm run typecheck && npm run build && cd ..
docker compose up --build -d
curl -s -o /dev/null -w '%{http_code}\n' localhost:5173/             # 200
curl -s localhost:5173/api/healthz                                  # {"status":"ok"}
```

Manual check (credentials or synthetic data): date selection, efficiency metrics, ledger, bottlenecks, risks, and narrative all display correctly.

---

## M12 Finalization

**Tasks**

1. Complete English README/submission notes per `11-readme-and-submission.md`, including required "AI assistance" (§7.5) and "With one more day" (§7.6). Draft AI disclosure truthfully: tools, uses, executed verification; mark human finalization required and never claim unperformed checks.
2. Review `docs/DECISIONS.md`: reasons for every deviation; separate entries for deferred real-data confidence calibration and chain-level delivery time.
3. Security: execute `12` security checks, including token-pattern history scan via `git log -p | grep`.
4. Performance: execute `12` performance checks.
5. Complete `12-acceptance-checklist.md` item by item (G6 in step 6).
6. Final step: prepare submission (`11` §9) after committing finalized README/DECISIONS with clean worktree and no secrets in history; generate `../delivery-insights.tar.gz` including `.git` and run all three checks (G6). Repackage after any later commit. **Do not** create a remote, push, or upload; the human does this (`AGENTS.md` §8).
7. Output the `AGENTS.md` §7 final report with absolute archive path; list human README `<confirm: …>` placeholders and human push/upload under "Pending human verification".

**DoD**: all `12-acceptance-checklist.md` items checked; credential-dependent or human-only items may be marked "Pending human verification".

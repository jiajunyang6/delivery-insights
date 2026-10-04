# Submission notes

## 1. How to run it locally

**Prerequisites:** Docker with Compose v2 (Docker Desktop on Windows/macOS). For live data, a
GitHub fine-grained personal access token: Settings → Developer settings → Fine-grained tokens,
**Repository access: Public repositories**, no extra permissions. A Bedrock API key is optional.

```bash
cp .env.example .env            # PowerShell: Copy-Item .env.example .env
# Edit .env:
#   GITHUB_TOKEN=github_pat_...          required for syncing GitHub data
#   AWS_BEARER_TOKEN_BEDROCK=...         optional; empty = deterministic template narrative
docker compose up --build -d
docker compose ps -a              # migrate exits 0; api, worker, web, postgres, redis are running
```

- Dashboard: <http://localhost:5173>. API docs: <http://localhost:8000/docs>. Both bind to localhost only.
- The worker starts syncing `bevyengine/bevy` immediately: 7 days first, then 30, then
  `BACKFILL_DAYS=120`. Last 7/30 days become available first. Until a period is covered, the
  API returns `202` with `Retry-After`, and the dashboard shows progress and retries.
- Other variables in `.env.example` are working defaults. To track more repositories, set
  `TRACKED_REPOS=owner/a,owner/b` and run `docker compose up -d` again.
- Without `GITHUB_TOKEN` the stack still starts; `/v1/repos` reports `missing_token`.
- Check status: `curl -s localhost:8000/readyz`, `curl -s localhost:8000/v1/repos`, `docker compose logs --tail=50 worker`.
- Stop: `docker compose down` keeps data; `docker compose down -v` resets the database.

Tests without Docker Compose (Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 24):
`make lint`, `make test` (integration tests need a running Docker daemon), `make eval-offline`,
and `cd frontend && npm ci && npm test && npm run build`.

## 2. Architecture and main decisions

The service has five containers. An **arq worker** is the only part that talks to GitHub. It
runs staged backfills, overlapping incremental syncs and open-PR sweeps over GraphQL/REST, writes
idempotent batches to **Postgres**, and derives a per-PR timeline. A state machine splits each
PR's life into non-overlapping intervals by whose turn it is: coding, waiting on a reviewer,
the author, CI, or merge. It also links reverts, relands and code owners. The **FastAPI** service
never calls GitHub on a request. It reads one consistent database view, runs pure analytics code
(no I/O imports), and returns an immutable snapshot. The snapshot ID hashes the parameters, data
watermark and code versions, which gives free ETags and caching. **Redis** holds jobs, locks,
caches and rate limits. A **React** dashboard is served by nginx on the same origin.

The narrative is built so the LLM cannot invent facts. Code selects numbered evidence (E1, E2…)
and scores four library hypotheses: review capacity, CI, PR size, and quality trade-off. Each
needs a symptom (something got worse than last period) and a mechanism, combined by a fixed
formula into an evidence-strength band. Claude Sonnet 4.6 only writes the wording, through a
tool call. A validator checks every number, citation and hedge word against the band. It allows
one repair, then falls back to a deterministic template. The model never sees PR titles,
comments or user names. If nothing slowed down, or evidence is weak, the narrative says so and
points to where PR time goes now.

Main decisions:

- **Metric:** PR cycle time and *where it waits*. It answers "slower, and why", unlike DORA-style counts.
- **Scope:** all sections use PRs opened or with human activity in the period, so a sprint view
  stays focused. The cost is that idle old PRs need a longer period to appear.
- **Confidence:** deterministic and testable, not model self-rated. It is synthetic-calibrated
  only (20-case eval: 14/16 root-cause hits, 4/4 correct abstentions).
- **Sync:** background sync rather than request-time fetching, for GitHub rate limits and fast reads.
- **Time:** UTC wall-clock, English only, public repos only.

Full trade-offs: [docs/REFERENCE.md](docs/REFERENCE.md#trade-offs-and-limitations);
per-decision log: [docs/DECISIONS.md](docs/DECISIONS.md).

## 3. With one more day

1. **Calibrate confidence on real history:** replay past periods and compare hypotheses with
   human-labelled causes. Today the scores are only checked on planted synthetic scenarios.
2. **Add a "Sync now" button** for tracked repositories in the dashboard. The API
   (`POST /v1/repos/{owner}/{name}/sync` plus job polling) already exists.
3. **Add a point-in-time "all open PRs" view** for backlog and at-risk stock, beside the
   period-active view.
4. **Harden sync:** reconcile jobs killed mid-run at startup, and retry GraphQL throttling
   returned with HTTP 200 and dropped connections.
5. **Scale linking for 180+ day backfills on large repos:** load fewer columns, match SHAs with
   sorted lists, and skip unchanged data.

## 4. How AI was used

- **Claude (Anthropic):** brainstorming, the design document, and the implementation plan in
  `docs/PLAN.md`, written as instructions for a coding agent. Later it reviewed the implementation
  diffs and found issues such as a causal-wording validator loophole, sync stalls on malformed
  PRs and stale pagination after sync. It also implemented the narrative abstention and
  hypothesis-card improvements.
- **Codex:** implemented the backend, frontend, migrations, tests, evaluation harness, containers
  and CI configuration from the plan, plus the fixes found in reviews.
- **Claude Sonnet 4.6 on Bedrock** is part of the product: it writes narrative wording only,
  and the deterministic validator decides whether it is shown.
- **What I did:** product roadmap, tech stack, high-level architecture and product decisions
  (the metric, the trade-offs, the period-scoped dashboard), plus plan and diff reviews.
- **How the output was checked:**
  - 402 automated tests, 69 of them on real Postgres 16 and Redis 7.
  - Strict ruff/mypy and the frontend typecheck and build.
  - The 20-case narrative evaluation (stub and real Bedrock on final prompt v8; rejected v8 trials retained).
  - Real GitHub sync and browser checks.
  - Refactor equivalence across golden, ten planted datasets and an ownership fixture;
    synthetic browser checks for both views, presets, Load more, cards and abstentions.
  - Not yet done: a person's numeric review of the golden fixture.
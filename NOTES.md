# Submission notes

## 1. How to run it locally

**Prerequisites:** Docker with Compose v2 (Docker Desktop on Windows/macOS). For live data, a
GitHub fine-grained personal access token: Settings → Developer settings → Fine-grained tokens,
**Repository access: Public repositories**, no extra permissions. A Bedrock API key is required
for LLM-generated narratives.

1. **Create the configuration file.** If `.env` does not exist, copy `.env.example`:

    Bash:

    ```bash
    cp .env.example .env
    ```

    PowerShell:

    ```powershell
    Copy-Item .env.example .env
    ```

2. **Edit and save `.env` before starting Docker.** Open `.env` in a text editor.

   - Set `GITHUB_TOKEN` to your GitHub token to enable syncing GitHub data.
   - Set `AWS_BEARER_TOKEN_BEDROCK` to your Bedrock API key to enable LLM-generated narratives.

   Without a Bedrock key, narratives use deterministic templates and no LLM calls are made.
   Save the file before continuing.

3. **Start the services and check their status.** Run these commands after saving `.env`:

    ```bash
    docker compose up --build -d
    docker compose ps -a
    ```

    Expected status: `migrate` has exited with code `0`; `api`, `worker`, `web`,
    `postgres` and `redis` are running.

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

![How Delivery Insights works](docs/diagrams/how-it-works.svg)

**01 — Local data and reporting:** the arq worker syncs GitHub history into Postgres and derives
PR timelines. FastAPI serves snapshots from local data; Redis supports jobs, caches, locks and
rate limits. The React dashboard accesses FastAPI through nginx.

**02 — Narrative generation:** code computes metrics, selects evidence and scores hypotheses.
Bedrock writes the wording; local validation checks numbers, citations and uncertainty language,
with at most one repair. A deterministic template handles disabled, busy or failed LLM calls
and replies that remain invalid. Validation and fallback run inside FastAPI; PR titles,
comments and user names are never sent to the LLM.

Main decisions:

- **Metric:** cycle time and where PRs wait, measured in UTC elapsed time. PR-hours describe
  waiting; finding ranks do not establish a cause or measure engineering effort.
- **Scope:** every section uses PRs opened or with human activity in the selected period.
  Older idle PRs may require a longer window to appear.
- **Reproducibility:** background sync keeps GitHub I/O off the request path. Pure analytics
  and snapshots identified by parameters, versions and watermarks support repeatable reports.
- **Evidence strength:** fixed rules score hypotheses, calibrated on synthetic scenarios only.
  Weak evidence or no slowdown leads to abstention; the score is not a probability.
- **Constraints:** English narratives and public repositories only.

Details: [technical reference](docs/REFERENCE.md#how-it-works),
[trade-offs](docs/REFERENCE.md#trade-offs-and-limitations),
[evaluation](docs/EVALUATION.md) and [decision log](docs/DECISIONS.md).

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

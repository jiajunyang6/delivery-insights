# Delivery Insights

Delivery Insights shows engineering managers **where pull request time goes** and whether
delivery slowed down for a reason the data supports. A background worker syncs GitHub PR
history into Postgres. One endpoint returns a deterministic insight for any period: the time
ledger of reviewer, author and merge waiting, the largest wait and shift, and median cycle
time. A second endpoint adds a short narrative written by Claude Sonnet 4.6 on Amazon Bedrock,
with root-cause hypotheses, a confidence score and an evidence chain. Every number comes from
code, and each claim in the narrative links to the number behind it.

Sections 1–4 are the submission notes. The sections after them summarize the product, API,
configuration and development; [docs/REFERENCE.md](docs/REFERENCE.md) has the details.

## 1. How to run it locally

**Prerequisites:**

- Docker with Compose v2 (Docker Desktop on Windows/macOS).
- A GitHub fine-grained personal access token for live data: Settings → Developer settings → Fine-grained tokens,
  **Repository access: Public repositories**, no extra permissions.
- A Bedrock API key is required for LLM-generated narratives.

**Steps:**

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

   - Set `GITHUB_TOKEN` to your GitHub token to enable syncing public GitHub data.
   - Set `AWS_BEARER_TOKEN_BEDROCK` to your Bedrock API key to enable LLM-generated narratives.
   - Set `AWS_REGION` to the region used for Bedrock requests (default: `us-west-2`).
   - Set `BEDROCK_MODEL_ID` to the model or inference profile ID used by the Converse API
     (default: `us.anthropic.claude-sonnet-4-6`).

   Choose a region and model or inference profile that your AWS account can access and has
   available quota for; change the defaults if your quota is available elsewhere.

   Without a Bedrock key, narratives use deterministic templates and no LLM calls are made.
   Save the file before continuing.

3. **Start the services and check their status.** Run these commands after saving `.env`:

    ```bash
    docker compose up --build -d
    docker compose ps -a
    ```

    Expected status: `migrate` has exited with code `0`; `api`, `worker`, `web`,
    `postgres` and `redis` are running.
    It might take up to 5 minutes to pull Github PR info and prepare the reports.

- Dashboard: <http://localhost:5173>. API docs: <http://localhost:8000/docs>. Both bind to localhost only.
- The worker starts syncing `bevyengine/bevy` immediately: 7 days first, then 30, then
  `BACKFILL_DAYS=120`. Last 7/30 days become available first. Until a period is covered, the
  API returns `202` with `Retry-After`, and the dashboard shows progress and retries.
- To track more repositories, set `TRACKED_REPOS=owner/repo_a,owner/repo_b` and run `docker compose up -d` again. Other variables in `.env` are working defaults.
- Without `GITHUB_TOKEN` the stack still starts; `/v1/repos` reports `missing_token`.
- Check status: `curl -s localhost:8000/readyz`, `curl -s localhost:8000/v1/repos`, `docker compose logs --tail=50 worker`.
- Stop: `docker compose down` keeps data; `docker compose down -v` resets the database.

## 2. Architecture and main decisions

![How Delivery Insights works](docs/diagrams/how-it-works.svg)

**Data sync and local reporting.** An arq worker pulls PR history from the GitHub GraphQL API
with a read-only token: a staged backfill (7, 30, then 120 days), incremental syncs every 15
minutes and periodic sweeps of open PRs. It stores raw records, per-PR timelines and derived
facts in Postgres, and precomputes the 7/30/60-day reports. FastAPI answers from this local
data only and never calls GitHub on a request. Each report is an immutable snapshot identified
by its parameters, code versions and sync watermarks, so it can be cached in Redis and Postgres
and served with an ETag; until a period is covered, the API returns `202` with sync progress.
Redis also holds the job queue, locks and rate limits. nginx serves the React dashboard and
proxies `/api` to FastAPI.

**Narrative generation.** Code builds an evidence pack from the snapshot and scores two
root-cause hypotheses, review capacity and PR size growth, with fixed rules. Bedrock receives
only that pack, never PR titles, bodies, comments or logins, and returns wording through a tool
call. A local validator checks every number, citation and hedge word against the pack. An
invalid reply gets one repair with the exact violations, and a deterministic template covers a
disabled, busy, failing or still-invalid LLM. Validated narratives are stored and reused, so
repeat requests make no LLM call.

**The metric and why I chose it.** The insight measures where the post-ready waiting time of
merged PRs goes (to reviewers, the author or merge) and how that split changed against the
previous period of equal length, alongside median cycle time. I chose it over contributor
leaderboards because it points to a team decision (review capacity, author follow-up or merge
policy) instead of ranking people, and because every hour traces back to a PR timeline.
PR-hours are elapsed waiting time, not effort; a large waiting share is a reason to look
closer, not a proven cause.

Main decisions:

- **Background sync over request-time fetching:** GitHub I/O and rate limits stay off the
  request path, and repeated queries cost nothing upstream.
- **Deterministic snapshots:** analytics is pure computation with no I/O, so the same data and
  parameters give the same bytes. This is what makes caching, ETags and a golden-file test
  possible.
- **One cohort everywhere:** every section counts PRs opened, or with human activity, in the
  period; bot and backport PRs are excluded from flow metrics. Older idle PRs need a longer
  window to appear.
- **Numbers in code, wording by the LLM:** the LLM never computes or scores. Confidence is an
  evidence-strength score from fixed rules, calibrated on synthetic scenarios only. It is not
  a probability, and weak evidence or no slowdown leads to abstention.
- **Project scope:** The broader scope gave the narrative more complete evidence, at the cost
  of additional implementation complexity. In retrospect, I would keep the initial delivery
  focused on the required path and add extensions only where their value justified the added
  complexity.

Trade-offs and what I chose not to do (more in
[REFERENCE.md](docs/REFERENCE.md#trade-offs-and-limitations)):

- **Freshness:** reports lag GitHub by up to one sync interval, and the first sync takes a few
  minutes before every period is ready.
- **Time basis:** UTC wall-clock time, including nights and weekends; no team calendars.
- **Locations:** current labels, otherwise the most-touched directories; historical labels and
  code ownership are not modelled.
- **Constraints:** one repository per request, public repositories and English narratives.
- **Not done:** individual productivity rankings, request-time GitHub fetching,
  authentication, webhooks, a second source adapter (the `SourceAdapter` protocol is the
  extension point) and deployment or release timing.

Beyond the brief: a React dashboard, the background worker with staged backfill, Postgres
storage with Redis and ETag caching, deterministic hypothesis scoring with a validator, repair
and template fallback, unit and integration tests, an offline and real-Bedrock evaluation
harness, Docker Compose and a CI workflow.

## 3. With one more day
I would do one of the followings if I had one more day: 
1. **Add a "Sync now" button** for tracked repositories in the dashboard, backed by a
   manual sync endpoint with a cooldown; the worker already runs on-demand sync jobs.
2. **Add a point-in-time "all open PRs" view** for backlog and at-risk stock, beside the
   period-active view.
3. **Speed up the first sync:** split the initial time window into date ranges and fetch them 
   in parallel using GraphQL search with updated: filters, instead of fetching one page at a time. 
   Also sync tracked repositories in parallel while staying within the shared GitHub rate limit. 
   Snapshot analytics already runs outside the event loop. If it becomes slow on large repositories, 
   move it to a process pool, because threads do not speed up CPU-heavy Python work.

## 4. How AI was used

- **My role:** I led the project, defined the scope, roadmap, tech stack and architecture,
  and made the product decisions around metrics, trade-offs and the period-scoped dashboard.
  I directed the AI-assisted work, reviewed the plans and code diffs, and made the final
  design decisions.
- **Claude (Anthropic):** supported brainstorming, drafting the design and implementation
  plan, code review and targeted improvements, under my lead and direction.
- **Codex (OpenAI):** assisted with implementation, fixes and verification across the backend,
  frontend, migrations, tests, evaluation harness, containers and CI, following my direction
  and review feedback.
- **Claude Sonnet 4.6 on Bedrock** is part of the product: it writes narrative wording only,
  and the deterministic validator decides whether it is shown.
- **How the output was checked:**
  - 322 backend tests, 63 of them on real Postgres 16 and Redis 7, plus 15 frontend tests.
  - Strict ruff/mypy and the frontend typecheck and build.
  - The current 15-case offline and real Bedrock evaluations on prompt v13 pass all eight
    gates. The real run is first-valid in 14/15 cases, the one invalid first answer is
    fixed by its repair, and no case falls back to the template; numeric, citation and
    hedge consistency are 15/15 each for final LLM outputs.
  - Real GitHub sync and browser checks.
  - CI removal checked field by field: every retained golden snapshot value is unchanged;
    synthetic browser checks cover presets, a custom period, pending, configuration, narrative
    and the three-state ledger. After the user's database rebuild, the Bevy worker completed
    the 120-day backfill and precomputed the 7/30/60-day reports.

## What you get

- **Repository and period:** a tracked repository and the last 7, 30 (default) or 60 days, or
  custom UTC dates, compared with the preceding period of equal length.
- **Sync progress:** until the period is covered, the page shows the sync stage and retries; a
  configuration notice names the `.env` setting to fix.
- **Narrative:** root-cause hypotheses scored by fixed rules, with every claim cited to its
  metric. When delivery did not slow down, or evidence is too weak, it says so and points to
  where PR time goes now.
- **Where PR time goes:** the time ledger of merged PRs across reviewer, author and merge
  waiting, current period against the previous one.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/insights/delivery?repo=…&from=…&to=…` | Insight for one repository and period: a one-paragraph statement, the largest wait, the largest shift, median cycle time and the time ledger; or `202` sync progress |
| GET | `/v1/snapshots/{snapshot_id}/narrative` | Cited narrative, hypotheses and evidence for that insight (`links.narrative`) |
| GET | `/v1/repos` | Tracked repositories, sync status, date limits and configuration health |
| GET | `/healthz`, `/readyz` | Liveness and Postgres/Redis readiness |

```bash
curl -s "http://localhost:8000/v1/insights/delivery?repo=bevyengine/bevy&from=2026-09-04&to=2026-10-03"
curl -s "http://localhost:8000/v1/snapshots/<snapshot_id>/narrative"
```

Dates are inclusive UTC dates. Insight and narrative responses carry an `ETag` and answer
`If-None-Match` with `304`. A `202` carries `Retry-After` and per-repository sync progress.
Errors use RFC 9457 `application/problem+json`: `422` for invalid input, `403` for an untracked
repository and `429` for the rate limit. Contract details:
[REFERENCE.md](docs/REFERENCE.md#api) and the strict models in
`backend/src/insights/api/schemas.py`.

## Configuration

Settings come from `.env`; never commit it. Without `.env`, the code defaults in
`backend/src/insights/config.py` apply.

| Variable | Default | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | empty | Required for sync; without it `/v1/repos` reports `missing_token` |
| `AWS_BEARER_TOKEN_BEDROCK` | empty | Required for LLM-generated narratives; without it, only deterministic templates are available |
| `AWS_REGION`, `BEDROCK_MODEL_ID` | `us-west-2`, `us.anthropic.claude-sonnet-4-6` | Bedrock Converse target |
| `TRACKED_REPOS` | `bevyengine/bevy` | Comma-separated allowlist, e.g. `bevyengine/bevy,prometheus/prometheus` |
| `BACKFILL_DAYS` | `120` | History to collect (30–365); the 60-day view needs at least 120 for a full comparison |
| `SYNC_INTERVAL_MINUTES` | `15` | Incremental sync cadence; must divide 60 |
| `PRECOMPUTE_DAYS` | `7,30,60` | Report windows warmed after a sync changes repository data |
| `LOCATION_DIMENSION` | `label:area-` | Area grouping: matching labels, otherwise directories |

Connection, CORS, rate-limit and logging settings are listed in
[REFERENCE.md](docs/REFERENCE.md#additional-settings).

## Development

```bash
make lint          # ruff + strict mypy
make test-unit     # backend unit tests
make test          # full suite; integration tests need Docker (Testcontainers)
make eval-offline  # 15-case synthetic narrative evaluation with a stub LLM
make eval          # same against real Bedrock (reads the key from .env)
cd frontend && npm ci && npm test && npm run typecheck && npm run build
```

Without Make (for example on Windows), run the `uv run` commands from the [Makefile](Makefile)
in `backend/`. Test scope and evaluation results are in
[REFERENCE.md](docs/REFERENCE.md#testing-and-evaluation).

The unreleased application keeps one migration, `0001_initial`, which is edited in place
until release. A database created by an earlier version of that file must be deleted and
synced again; an in-place upgrade is unsupported. After explicitly confirming that
collected PRs, snapshots and narratives may be deleted, run:

```bash
docker compose down -v
docker compose up -d --build
```

The worker then collects history again from an empty database. Wait for repository
`last_sync_status=ok` and full coverage before checking the dashboard, and keep
`BACKFILL_DAYS=120` for the 60-day view and its comparison period.

## Documentation

| Document | Contents |
|---|---|
| [docs/REFERENCE.md](docs/REFERENCE.md) | Metric semantics, pipeline, API and insight contract, confidence scoring, operations, security, test and evaluation results, limitations |
| [AGENTS.md](AGENTS.md) | Repository map, invariants and verification steps for coding agents |

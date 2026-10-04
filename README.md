# Delivery Insights

Delivery Insights shows engineering managers and directors **where pull requests wait** and
which evidence supports an explanation. It syncs GitHub PR history in the background, computes
deterministic delivery metrics and bottlenecks, and adds a cited narrative written by Claude
Sonnet 4.6 on Amazon Bedrock. Each claim in the narrative links to the number behind it, and every number comes from code.

**Reviewers:** [NOTES.md](NOTES.md) covers how to run it, the architecture, next steps and AI use.

## Quickstart

Prerequisites: Docker with Compose v2, and a GitHub fine-grained token with
**Public repositories** access and no extra permissions. A Bedrock API key is required for
LLM-generated narratives.

1. **Create the configuration file.** Copy `.env.example` if `.env` does not already exist:

    Bash:

    ```bash
    [ -f .env ] || cp .env.example .env
    ```

    PowerShell:

    ```powershell
    if (!(Test-Path .env)) { Copy-Item .env.example .env }
    ```

2. **Edit and save `.env` before starting Docker.** Open `.env` in a text editor.

   - Set `GITHUB_TOKEN` to your GitHub token to enable syncing GitHub data.
   - Set `AWS_BEARER_TOKEN_BEDROCK` to your Bedrock API key to enable LLM-generated narratives.

   Without a Bedrock key, narratives use deterministic templates and no LLM calls are made.
   Save the file before continuing.

3. **Start the services.** Run this command after saving `.env`:

    ```bash
    docker compose up --build -d
    ```

Open the dashboard at <http://localhost:5173> and the API docs at <http://localhost:8000/docs>.
The worker backfills `bevyengine/bevy` in 7-day, 30-day and then `BACKFILL_DAYS` (120) stages.
Until a period is covered, the API returns `202` with `Retry-After` and the dashboard shows progress.

## What you get

- **Periods:** Last 7, 30 (default) or 60 days, compared with the preceding period of equal length.
  Every section counts PRs opened, or with recorded human activity, during the period.
- **Delivery Overview:** delivery outcomes (cycle time p50/p90, throughput, merged within 3 days,
  waiting share, waste, review rounds and concentration, reverts), the evidence narrative,
  the time ledger and the top three bottlenecks.
- **PR & Review Details:** all bottlenecks, the review queue, a per-area table and at-risk PRs
  (five rows, then **Load more**).
- **Narrative:** root-cause hypotheses scored by fixed rules. When delivery did not slow down, or
  evidence is too weak, it says so and points to where PR time goes now.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/insights/delivery?repo=…&from=…&to=…` | Metrics, time ledger, bottlenecks and at-risk PRs (immutable snapshot) |
| GET | `/v1/snapshots/{snapshot_id}/narrative?audience=director\|manager` | Cited narrative, hypotheses and evidence for that snapshot |
| GET | `/v1/insights/delivery/prs` | Filtered, paginated PR drilldown |
| GET | `/v1/snapshots/{snapshot_id}` | Read a retained snapshot |
| GET | `/v1/repos` | Tracked repositories, freshness and sync status |
| POST | `/v1/repos/{owner}/{name}/sync` | Enqueue a manual sync (`GET /v1/sync-jobs/{id}` for progress) |
| GET | `/healthz`, `/readyz` | Liveness and Postgres/Redis readiness |

```bash
curl -s "http://localhost:8000/v1/insights/delivery?repo=bevyengine/bevy&from=2026-09-04&to=2026-10-03"
```

Dates are inclusive UTC dates. Errors use RFC 9457 `application/problem+json`. Current contract: [REFERENCE.md](docs/REFERENCE.md#api) and the strict models in
`backend/src/insights/api/schemas.py`. The original design is retained in `docs/PLAN.md`.

## Configuration

Settings come from `.env`; never commit it. Without `.env`, code defaults apply (`backend/src/insights/config.py`):
`TRACKED_REPOS=dotnet/runtime` and `BACKFILL_DAYS=120`.

| Variable | `.env.example` | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | empty | Required for sync; without it `/v1/repos` reports `missing_token` |
| `AWS_BEARER_TOKEN_BEDROCK` | empty | Required for LLM-generated narratives; without it, only deterministic templates are available |
| `AWS_REGION`, `BEDROCK_MODEL_ID` | `us-west-2`, `us.anthropic.claude-sonnet-4-6` | Bedrock Converse target |
| `TRACKED_REPOS` | `bevyengine/bevy` | Comma-separated allowlist, e.g. `bevyengine/bevy,prometheus/prometheus` |
| `BACKFILL_DAYS` | `120` | History to collect (30–365); the 60-day view needs at least 120 for a full comparison |
| `PRECOMPUTE_DAYS` | `7,30,60` | Snapshot windows warmed after a successful sync changes repository data |
| `LOCATION_DIMENSION` | `label:area-` | Area grouping: labels, then CODEOWNERS, then directories |
| `CI_SOURCE`, `CI_COMPLETE` | `actions`, `false` | GitHub Actions CI waiting; `false` caps CI-hypothesis confidence |

## Development

```bash
make lint          # ruff + strict mypy
make test-unit     # backend unit tests
make test          # full suite; integration tests need Docker (Testcontainers)
make eval-offline  # 20-case synthetic narrative evaluation with a stub LLM
make eval          # same against real Bedrock (reads the key from .env)
cd frontend && npm ci && npm test && npm run typecheck && npm run build
```

Analytics version **1.5.0** trims unused snapshot diagnostics while retaining dashboard,
narrative and drilldown fields. Snapshot/cache identities use 1.5.0; statistical sampling
keeps the original 1.4.0 seed parameters.

The unreleased application's migrations are now consolidated into `0001_initial`.
Upgrading from the earlier three-migration schema requires deleting the local database
and syncing again; an in-place upgrade is unsupported. After explicitly confirming that
collected PRs, snapshots and narratives may be deleted, run:

```bash
docker compose down -v
docker compose up -d --build
```

The worker resumes collection from an empty database, with 7-, 30- and 120-day backfill
checkpoints under the default configuration. Wait for repository `last_sync_status=ok`
and full coverage before checking the dashboard. Keep `BACKFILL_DAYS=120` for the 60-day
view and its comparison period.

Local checks: **402 backend tests** (333 unit, 69 integration), **9 frontend tests**,
strict lint/types/build and all eight offline narrative gates pass.

## Documentation

| Document | Contents |
|---|---|
| [NOTES.md](NOTES.md) | Submission notes: run, architecture, next steps, AI use |
| [docs/REFERENCE.md](docs/REFERENCE.md) | Metric definitions, confidence scoring, operations, security, test results, limitations |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Implementation decisions and their reasons |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Narrative evaluation runs, per case |
| [docs/plan/PLAN.md](docs/PLAN.md) | Consolidated historical design input; current code and reference take precedence |

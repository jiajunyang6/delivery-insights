# Delivery Insights

Delivery Insights turns GitHub pull request timelines into delivery metrics, waiting-time
bottlenecks and evidence-backed explanations. All numeric analysis runs in Python.

## Quickstart

Requirements: Docker Engine with Compose. For local development: Python 3.12 and uv.

```sh
cp .env.example .env
# Set TRACKED_REPOS and optionally GITHUB_TOKEN in .env.
docker compose up --build -d
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
curl http://localhost:8000/v1/repos
```

API documentation: [http://localhost:8000/docs](http://localhost:8000/docs).

No credentials are needed for unit tests, synthetic analytics or the forthcoming offline
evaluation. Live ingestion requires GITHUB_TOKEN. Missing credentials do not prevent the
health endpoints from working; repository status explains why live data is unavailable.

## API examples

Replace the dates with a period within the configured backfill horizon.

```sh
curl -i 'http://localhost:8000/v1/insights/delivery?repo=dotnet/runtime&from=2026-09-03&to=2026-10-02'
curl -i -H 'If-None-Match: "<etag from the previous response>"' 'http://localhost:8000/v1/insights/delivery?repo=dotnet/runtime'
curl -i 'http://localhost:8000/v1/snapshots/<snapshot_id>'
curl -i 'http://localhost:8000/v1/insights/delivery/prs?repo=dotnet/runtime&at_risk=true&limit=5'
curl -i -X POST 'http://localhost:8000/v1/repos/dotnet/runtime/sync'
```

Unready data returns 202 with retry guidance. GET does not enqueue synchronization.
Untracked repositories return 403; invalid parameters return 422 problem+json.
Manual synchronization has a per-repository cooldown.

## Local checks

```sh
cd backend
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -m 'not integration'
uv run pytest
```

The full test suite starts isolated Postgres and Redis containers. GNU Make provides
equivalent root-level targets. On Windows, use the uv commands directly if make is absent.

## Operations and current limitations

Snapshot links expire after seven days. Daily housekeeping removes expired snapshots,
their narratives and cache entries, plus sync jobs finished more than thirty days ago.
Redis failure bypasses caching and request rate limiting; manual enqueue requires Redis.
Read-only snapshot transactions provide a consistent view while ingestion continues.
Containers run as a non-root user and the API binds to loopback by default.

The API intentionally has no authentication. Requests through the planned nginx UI share
the proxy's rate-limit bucket. Durations are UTC wall-clock hours, not business hours.
Commit timestamps approximate push times. Temporarily closed intervals are excluded from
waiting ledgers but retained in milestone elapsed durations. Linked revert, reland and
supersession records are available; chain-level delivery duration is deferred.

Implementation and verification status: [docs/PROGRESS.md](docs/PROGRESS.md).
Trade-offs: [docs/DECISIONS.md](docs/DECISIONS.md).

# 02 Configuration, dependencies, and infrastructure

## 1. Configuration (`insights/config.py`)

Use pydantic-settings `BaseSettings`, reading environment variables with `model_config = SettingsConfigDict(case_sensitive=False, env_ignore_empty=True)`. Empty strings count as unset: blank example tokens become `None`, rather than empty `SecretStr` values mistakenly interpreted as present. Docker Compose injects `.env` for local development; application code must not read that file directly.

**Store list configuration as strings and parse via properties** to avoid pydantic-settings JSON parsing. Strip whitespace, omit empty entries, validate as below, and reject invalid values at startup.

| Environment variable | Field type | Default | Description |
|---|---|---|---|
| `GITHUB_TOKEN` | `SecretStr \| None` | `None` | Required for worker synchronization, not API; absent token fails sync with `missing_token` |
| `GITHUB_API_URL` | `str` | `https://api.github.com` | Configuration only; no request override |
| `GITHUB_GRAPHQL_URL` | `str` | `https://api.github.com/graphql` | Same restriction |
| `TRACKED_REPOS` | `str` (comma-separated) | `dotnet/runtime` | `tracked_repo_list: list[str]`; validate repository pattern (`06` §3.1), compare lowercase, retain display casing |
| `LOCATION_DIMENSION` | `str` | `label:area-` | `label:<prefix>`, `codeowners`, or `directory` |
| `DIRECTORY_DEPTH` | `int` | `2` | Leading path segments for directory dimension, 1–3 |
| `BACKFILL_DAYS` | `int` | `120` | 30–365; stages `[7, 30, BACKFILL_DAYS]`, deduplicated and sorted |
| `SYNC_INTERVAL_MINUTES` | `int` | `15` | Must divide 60 |
| `OPEN_SWEEP_MINUTES` | `int` | `60` | Full open-PR sweep interval; multiple of `SYNC_INTERVAL_MINUTES` |
| `GRAPHQL_PAGE_SIZE` | `int` | `25` | 5–50; automatically halved on runtime failures (`04` §4.3) |
| `EXTRA_BOT_LOGINS` | `str` (comma-separated) | Empty | Extend built-in bot list (`04` §5.2) |
| `DATABASE_URL` | `str` | `postgresql+asyncpg://insights:insights@postgres:5432/insights` | |
| `REDIS_URL` | `str` | `redis://redis:6379/0` | |
| `AWS_BEARER_TOKEN_BEDROCK` | `SecretStr \| None` | `None` | Determines LLM enablement only; boto3 reads the environment itself |
| `AWS_REGION` | `str` | `us-west-2` | |
| `BEDROCK_MODEL_ID` | `str` | `us.anthropic.claude-sonnet-4-6` | Claude Sonnet 4.6 on-demand calls require an inference-profile ID |
| `LLM_TIMEOUT_SECONDS` | `int` | `60` | Read timeout per Bedrock call |
| `CORS_ORIGINS` | `str` (comma-separated) | `http://localhost:5173` | |
| `RATE_LIMIT_PER_MINUTE` | `int` | `120` | Per client IP |
| `MANUAL_SYNC_COOLDOWN_SECONDS` | `int` | `300` | Manual synchronization cooldown |
| `MAX_REPOS_PER_REQUEST` | `int` | `20` | |
| `PRECOMPUTE_DAYS` | `str` (comma-separated) | `7,30,90` | Period lengths precomputed after synchronization |
| `CI_SOURCE` | `Literal["actions", "none"]` | `actions` | P1 |
| `CI_COMPLETE` | `bool` | `false` | P1: whether Actions is the repository's main CI. False caps CI-hypothesis confidence at 0.5 (`07` §4.3). Keep false for dotnet/runtime's Azure Pipelines CI |
| `AREA_OWNERS_PATH` | `str` | `docs/area-owners.md` | P1; skip if absent in upstream repository |
| `LOG_LEVEL` | `str` | `INFO` | |

Derived properties:

- `llm_enabled: bool` = `AWS_BEARER_TOKEN_BEDROCK` is nonempty.
- `backfill_phases: list[int]`.
- `location_label_prefix: str | None` when `LOCATION_DIMENSION` starts with `label:`.

## 2. `backend/pyproject.toml`

```toml
[project]
name = "delivery-insights"
version = "1.0.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "sqlalchemy[asyncio]>=2.0.30",
  "asyncpg>=0.29",
  "alembic>=1.13",
  "redis>=5.0",
  "arq>=0.26",
  "httpx>=0.27",
  "boto3>=1.40",
  "numpy>=2.0",
  "orjson>=3.10",
  "structlog>=24.1",
  "pathspec>=0.12",
]

[dependency-groups]
dev = [
  "pytest>=8.2",
  "pytest-asyncio>=0.23",
  "respx>=0.21",
  "testcontainers[postgres,redis]>=4.4",
  "ruff>=0.5",
  "mypy>=1.10",
  "boto3-stubs[bedrock-runtime]>=1.40",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/insights", "eval/insights_eval"]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "eval", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "RUF", "S", "ASYNC", "PTH", "N", "C4", "PIE", "RET"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "S105", "S106", "S311"]
"eval/**" = ["S311"]

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
mypy_path = ["src", "eval"]
packages = ["insights", "insights_eval"]

[[tool.mypy.overrides]]
module = ["arq.*", "testcontainers.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = ["integration: needs Docker (testcontainers)"]
addopts = "-ra --strict-markers"
```

Allow `S311` (non-cryptographic randomness) only in synthetic data and tests; production bootstrap uses `numpy.random.default_rng(seed)`.

## 3. Code conventions

- Module boundaries: `analytics/` and `narrative/` are pure functions except `dataset.py`, `service.py`, `llm.py`; do not import `db`, `redis`, `httpx`, `boto3` in pure modules.
- Types: domain objects use `@dataclass(frozen=True, slots=True)`; API contracts use Pydantic models; keep them separate.
- Names: English, full words; timestamps end in `_at`, durations in `_hours`, ratios in `_share` or `_rate` (0–1).
- Exceptions: a small meaningful hierarchy (`GitHubAuthError`, `GitHubNotFoundError`, `GitHubRateLimited`, `LLMUnavailable`, etc.). Do not silently swallow errors with broad `except Exception`; log before required fallbacks such as narrative degradation.
- No commented-out code; comments explain why rather than repeat implementation.

## 4. Logging (`insights/logging.py`)

- structlog JSON via orjson: at least `timestamp` (ISO 8601 UTC), `level`, `event`, `logger`; API requests add `request_id`, `method`, `path`, `status`, `duration_ms`; worker jobs add `job`, `repo`, `phase`.
- Standard-library logging uses structlog `ProcessorFormatter` for the same JSON. Disable `uvicorn.access` (middleware logs requests); set `httpx`, `httpcore`, `botocore`, `urllib3` to `WARNING`.
- Never log request headers or complete upstream response bodies.

## 5. `backend/Dockerfile`

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir "uv>=0.4"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY eval ./eval
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN useradd --create-home --uid 10001 app
COPY --from=builder /app /app
COPY alembic.ini ./
COPY migrations ./migrations
USER app
EXPOSE 8000
CMD ["uvicorn", "insights.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
```

`backend/.dockerignore`:`.venv`, `**/__pycache__`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `tests`, `reports`.

## 6. `docker-compose.yml` (final form; migrate added in M1, worker in M3, web in M11)

```yaml
name: delivery-insights

x-backend: &backend
  build: ./backend
  image: delivery-insights-backend:local
  env_file: .env

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: insights
      POSTGRES_PASSWORD: insights
      POSTGRES_DB: insights
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U insights -d insights"]
      interval: 5s
      timeout: 3s
      retries: 20

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 20

  migrate:
    <<: *backend
    command: ["alembic", "upgrade", "head"]
    depends_on:
      postgres: { condition: service_healthy }
    restart: "no"

  api:
    <<: *backend
    ports:
      - "8000:8000"
    depends_on:
      migrate: { condition: service_completed_successfully }
      redis: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status == 200 else 1)"]
      interval: 10s
      timeout: 3s
      retries: 10

  worker:
    <<: *backend
    command: ["arq", "insights.sync.worker.WorkerSettings"]
    depends_on:
      migrate: { condition: service_completed_successfully }
      redis: { condition: service_healthy }

  web:
    build: ./frontend
    ports:
      - "5173:8080"          # nginx-unprivileged listens on 8080 as non-root (09 §6)
    depends_on:
      api: { condition: service_healthy }

volumes:
  pgdata:
```

Do not expose Postgres/Redis host ports; use `docker compose exec` when needed.

## 7. `Makefile` (commands require Tab indentation)

```make
.PHONY: up down logs lint fmt test test-unit eval eval-offline smoke

up:
	docker compose up --build -d
down:
	docker compose down
logs:
	docker compose logs -f api worker
lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
fmt:
	cd backend && uv run ruff format . && uv run ruff check --fix .
test:
	cd backend && uv run pytest
test-unit:
	cd backend && uv run pytest -m "not integration"
eval:
	cd backend && uv run python -m insights_eval.run --llm bedrock
eval-offline:
	cd backend && uv run python -m insights_eval.run --llm stub
smoke:
	./scripts/smoke.sh
```

`scripts/smoke.sh` (created M6, extended M7/M11): call `/healthz`, `/readyz`, `/v1/repos`, last-seven-day `/v1/insights/delivery` (poll 202 using `Retry-After` for at most five minutes), then snapshot narrative. Print status codes/key fields; exit nonzero on any failure. Use `set -euo pipefail`; dependencies only `curl`, `python3`.

## 8. Root files

`.env.example`:

```dotenv
# Required for syncing: fine-grained personal access token, Repository access = "Public repositories", no extra permissions
GITHUB_TOKEN=
# Optional: Amazon Bedrock API key. Without it the narrative endpoint falls back to a deterministic template.
AWS_BEARER_TOKEN_BEDROCK=
AWS_REGION=us-west-2
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-6
TRACKED_REPOS=dotnet/runtime
LOCATION_DIMENSION=label:area-
BACKFILL_DAYS=120
SYNC_INTERVAL_MINUTES=15
CORS_ORIGINS=http://localhost:5173
LOG_LEVEL=INFO
DATABASE_URL=postgresql+asyncpg://insights:insights@postgres:5432/insights
REDIS_URL=redis://redis:6379/0
```

`.gitignore` includes at least `.env`, `.venv/`, `__pycache__/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `node_modules/`, `frontend/dist/`, `backend/reports/`, `*.egg-info/`.

## 9. CI(`.github/workflows/ci.yml`)

Two jobs, triggered by `push` and `pull_request`:

- `backend` (ubuntu-latest): Python 3.12 + uv → `uv sync` → `ruff check` → `ruff format --check` → `mypy` → `pytest -m "not integration"` → `pytest -m integration` (GitHub-hosted runners include Docker).
- `frontend` (enabled after M11): Node 20 → `npm ci` → `npm run typecheck` → `npm run build`.

CI requires no secrets.

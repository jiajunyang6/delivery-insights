# 02 配置、依赖与基础设施

## 1. 配置项（`insights/config.py`）

用 `pydantic-settings` 的 `BaseSettings`，从环境变量读取，`model_config = SettingsConfigDict(case_sensitive=False, env_ignore_empty=True)`：空字符串视为未设置，所以 `.env.example` 里留空的 token 得到 `None`，而不是空的 `SecretStr`（否则会被误判为"有 token"）。`.env` 只在本地开发时由 Docker Compose 注入，代码里不要主动读取 `.env` 文件。

**列表类配置一律存成字符串字段，再用属性解析**（避免 pydantic-settings 把环境变量当 JSON 解析）。解析时去掉空白、忽略空项，并按下表的规则校验，非法值在启动时报错。

| 环境变量 | 字段类型 | 默认值 | 说明 |
|---|---|---|---|
| `GITHUB_TOKEN` | `SecretStr \| None` | `None` | worker 同步必需；API 不需要。缺失时同步任务失败并记录 `missing_token` |
| `GITHUB_API_URL` | `str` | `https://api.github.com` | 只来自配置，不接受请求参数覆盖 |
| `GITHUB_GRAPHQL_URL` | `str` | `https://api.github.com/graphql` | 同上 |
| `TRACKED_REPOS` | `str`（逗号分隔） | `dotnet/runtime` | 属性 `tracked_repo_list: list[str]`，每项匹配仓库正则（`06` §3.1），转小写比较、保留原始大小写显示 |
| `LOCATION_DIMENSION` | `str` | `label:area-` | 取值 `label:<prefix>`、`codeowners`、`directory` |
| `DIRECTORY_DEPTH` | `int` | `2` | 目录维度取路径前几段，1–3 |
| `BACKFILL_DAYS` | `int` | `180` | 30–365；回填阶段为 `[7, 30, BACKFILL_DAYS]`（去重、升序） |
| `SYNC_INTERVAL_MINUTES` | `int` | `15` | 必须整除 60 |
| `OPEN_SWEEP_MINUTES` | `int` | `60` | 开着的 PR 全量扫描间隔，必须是 `SYNC_INTERVAL_MINUTES` 的整数倍 |
| `GRAPHQL_PAGE_SIZE` | `int` | `25` | 5–50，失败时运行期自动减半（`04` §4.3） |
| `EXTRA_BOT_LOGINS` | `str`（逗号分隔） | 空 | 追加到内置 bot 名单（`04` §5.2） |
| `DATABASE_URL` | `str` | `postgresql+asyncpg://insights:insights@postgres:5432/insights` | |
| `REDIS_URL` | `str` | `redis://redis:6379/0` | |
| `AWS_BEARER_TOKEN_BEDROCK` | `SecretStr \| None` | `None` | 只用于判断是否启用 LLM；boto3 自己从环境变量读取 |
| `AWS_REGION` | `str` | `us-west-2` | |
| `BEDROCK_MODEL_ID` | `str` | `us.anthropic.claude-sonnet-4-6` | Claude Sonnet 4.6 按需调用必须用推理配置 ID |
| `LLM_TIMEOUT_SECONDS` | `int` | `60` | 单次 Bedrock 调用读超时 |
| `CORS_ORIGINS` | `str`（逗号分隔） | `http://localhost:5173` | |
| `RATE_LIMIT_PER_MINUTE` | `int` | `120` | 每个客户端 IP |
| `MANUAL_SYNC_COOLDOWN_SECONDS` | `int` | `300` | 手动触发同步的冷却时间 |
| `MAX_REPOS_PER_REQUEST` | `int` | `20` | |
| `PRECOMPUTE_DAYS` | `str`（逗号分隔） | `7,30,90` | 同步后预计算的周期长度 |
| `CI_SOURCE` | `Literal["actions", "none"]` | `actions` | P1 |
| `CI_COMPLETE` | `bool` | `false` | P1；GitHub Actions 是否就是仓库的主 CI。为 `false` 时 CI 相关假设的置信度封顶 0.5（`07` §4.3）。dotnet/runtime 的主 CI 在 Azure Pipelines，保持 `false` |
| `AREA_OWNERS_PATH` | `str` | `docs/area-owners.md` | P1；仓库里不存在该文件时跳过 |
| `LOG_LEVEL` | `str` | `INFO` | |

派生属性：

- `llm_enabled: bool` = `AWS_BEARER_TOKEN_BEDROCK` 非空。
- `backfill_phases: list[int]`。
- `location_label_prefix: str | None`（`LOCATION_DIMENSION` 为 `label:` 时）。

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

`S311`（非加密随机数）只在合成数据和测试里放开；生产代码里的随机过程（bootstrap）用 `numpy.random.default_rng(seed)`。

## 3. 代码规范

- 模块边界：`analytics/` 和 `narrative/` 中除 `dataset.py`、`service.py`、`llm.py` 外都是纯函数，不导入 `db`、`redis`、`httpx`、`boto3`。
- 数据类：领域对象用 `@dataclass(frozen=True, slots=True)`；API 契约用 Pydantic 模型；两者不要混用。
- 命名：英文、完整单词；时间字段统一 `_at` 结尾，时长统一 `_hours` 结尾，比例统一 `_share` 或 `_rate` 结尾（取值 0–1）。
- 异常：定义少量有意义的异常类（`GitHubAuthError`、`GitHubNotFoundError`、`GitHubRateLimited`、`LLMUnavailable` 等），不要裸 `except Exception` 吞掉错误；需要兜底的地方（叙述降级）记录日志后再降级。
- 不写注释掉的代码；注释解释"为什么"，不复述代码。

## 4. 日志（`insights/logging.py`）

- structlog 输出 JSON（用 orjson 渲染），字段至少包括：`timestamp`（ISO 8601 UTC）、`level`、`event`、`logger`；API 请求额外有 `request_id`、`method`、`path`、`status`、`duration_ms`；worker 任务额外有 `job`、`repo`、`phase`。
- 标准库 logging 通过 structlog 的 `ProcessorFormatter` 输出同样的 JSON。`uvicorn.access` 关闭（访问日志由中间件输出），`httpx`、`httpcore`、`botocore`、`urllib3` 设为 `WARNING`。
- 永远不要记录请求头或完整的上游响应体。

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

`backend/.dockerignore`：`.venv`、`**/__pycache__`、`.mypy_cache`、`.ruff_cache`、`.pytest_cache`、`tests`、`reports`。

## 6. `docker-compose.yml`（最终形态；`migrate` 在 M1、`worker` 在 M3、`web` 在 M11 加入）

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
      - "5173:8080"          # nginx-unprivileged 以非 root 用户监听 8080（09 §6）
    depends_on:
      api: { condition: service_healthy }

volumes:
  pgdata:
```

Postgres 和 Redis 不映射到主机端口；需要时用 `docker compose exec`。

## 7. `Makefile`（命令行必须用 Tab 缩进）

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

`scripts/smoke.sh`（M6 创建，M7/M11 补充）：依次调用 `/healthz`、`/readyz`、`/v1/repos`、最近 7 天的 `/v1/insights/delivery`（遇到 202 时按 `Retry-After` 轮询，最多 5 分钟）、对应快照的叙述，打印每一步的状态码和关键字段；任何一步失败以非零退出。`set -euo pipefail`，只依赖 `curl` 和 `python3`。

## 8. 根目录文件

`.env.example`：

```dotenv
# Required for syncing: fine-grained personal access token, Repository access = "Public repositories", no extra permissions
GITHUB_TOKEN=
# Optional: Amazon Bedrock API key. Without it the narrative endpoint falls back to a deterministic template.
AWS_BEARER_TOKEN_BEDROCK=
AWS_REGION=us-west-2
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-6
TRACKED_REPOS=dotnet/runtime
LOCATION_DIMENSION=label:area-
BACKFILL_DAYS=180
SYNC_INTERVAL_MINUTES=15
CORS_ORIGINS=http://localhost:5173
LOG_LEVEL=INFO
DATABASE_URL=postgresql+asyncpg://insights:insights@postgres:5432/insights
REDIS_URL=redis://redis:6379/0
```

`.gitignore` 至少包含：`.env`、`.venv/`、`__pycache__/`、`.mypy_cache/`、`.ruff_cache/`、`.pytest_cache/`、`node_modules/`、`frontend/dist/`、`backend/reports/`、`*.egg-info/`。

## 9. CI（`.github/workflows/ci.yml`）

两个 job，触发条件为 `push` 和 `pull_request`：

- `backend`（ubuntu-latest）：安装 Python 3.12 和 uv → `uv sync` → `ruff check` → `ruff format --check` → `mypy` → `pytest -m "not integration"` → `pytest -m integration`（GitHub 托管的 runner 自带 Docker）。
- `frontend`（M11 后启用）：Node 20 → `npm ci` → `npm run typecheck` → `npm run build`。

CI 不需要任何密钥。

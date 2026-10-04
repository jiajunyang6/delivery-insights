# Repository Guidelines

## Project Structure & Module Organization

- `backend/src/insights/`: FastAPI routes, GitHub adapters, sync workers, database access, analytics, and narratives.
- `backend/migrations/`: Alembic schema revisions. `backend/tests/`: unit/integration suites, fixtures, and golden snapshots. `backend/eval/`: synthetic scenarios and evaluation harness.
- `backend/src/insights/snapshots/`: shared snapshot orchestration, caching, filters and domain errors; services must not import `api.*`.
- `backend/src/insights/sync/queue.py`: job lifecycle/locking; `sync/derive.py`: derivation and rederivation. PR rows live in `analytics/snapshot.py`; pointer resolution stays independent.
- `frontend/src/`: React/TypeScript dashboard, components, shared `format.ts`, `hooks/useAbortable.ts`, and CSS assets.
- `docs/PLAN.md`: consolidated historical requirements; `docs/DECISIONS.md`: architectural decisions; `docs/EVALUATION.md`: evaluation records.
- `scripts/smoke.sh`: HTTP smoke checks; `.github/workflows/ci.yml`: CI gates.

## Build, Test, and Development Commands

Use Python 3.12, uv, Node 24, and Docker Compose v2. Preserve lockfiles.

- In `backend/`, run `uv sync --frozen`; in `frontend/`, run `npm ci`.
- Copy `.env.example` to `.env` if absent, then run `docker compose up --build -d` from the root. Dashboard: `http://localhost:5173`; API docs: `http://localhost:8000/docs`.
- `make lint`: Ruff lint/format checks and strict mypy. `make fmt`: apply backend formatting and lint fixes.
- `make test-unit`: tests without Docker. `make test`: full suite, including container integration tests.
- `make eval-offline`: synthetic evaluation with stub LLM. `make eval`: credentialed Bedrock evaluation. `make smoke`: live HTTP checks.
- In `frontend/`, use `npm run dev`, `npm run typecheck`, and `npm run build` for development, type checking, and production output.

On Windows without Make, use the equivalent `uv run` commands documented in `README.md`.

## Coding Style & Naming Conventions

Use four-space Python indentation, type annotations, snake_case functions/modules, and PascalCase classes. Ruff targets 100-character lines; mypy is strict. Match frontend two-space indentation, double quotes, semicolons, PascalCase component files, and camelCase helpers. TypeScript uses strict checking. Write documentation and user-facing prose in English.

## Testing Guidelines

Use pytest/pytest-asyncio; name files `test_*.py` and functions `test_*`. Mark integration tests `integration`; Testcontainers requires Docker for PostgreSQL 16 and Redis 7. Mock GitHub/Bedrock calls. Cover changed behavior, timeline invariants, deterministic snapshots, and narrative validation. Current suite: 402 backend tests (333 unit, 69 integration) and 8 frontend tests. No minimum coverage percentage is configured. For UI changes, run typecheck/build and verify affected browser flows.

## Commit & Pull Request Guidelines

Follow history's Conventional Commits: `fix(analytics): ...`, `feat(web): ...`, or `docs: ...`. Keep commits focused. PRs should explain behavior changes, link relevant issues or plan sections, report checks and pending verification, and include screenshots for UI changes.

## Architecture & Configuration

Analytics/cache version is 1.5.0. Keep `SAMPLING_SEED_VERSION=1.4.0` unless intentionally changing statistical sampling; strict snapshot schemas match the retained outputs. Keep analytics free of database/HTTP I/O; API requests read local data, while workers ingest GitHub. Apply the same period-active cohort across metrics, risks, narratives, and drilldowns. Keep `.env` and credentials out of Git/logs; send only structured, sanitized evidence to the LLM.

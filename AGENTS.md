# AGENTS.md

Instructions for coding agents working in this repository. Read this file first; `README.md`
describes the product and submission, `docs/REFERENCE.md` holds the current contracts.

## Ground rules

- Ask the user before anything destructive or irreversible: `docker compose down -v`, deleting
  data or files, force-pushing, or rewriting published history.
- Commits use Conventional Commits (`fix(analytics): ...`, `feat(web): ...`, `docs: ...`), stay
  focused, and are authored by the repository owner. Never add AI attribution trailers
  (`Co-Authored-By`, session links) or commit under an AI identity.
- Never commit `.env`, credentials, local overrides (`docker-compose.override.yml`) or working
  notes such as `REFACTOR_PLAN.md`. Keep tokens and secrets out of logs.
- Code, docs and all user-facing text are English. Files use LF line endings (`.gitattributes`).

## Repository map

| Path | Responsibility |
|---|---|
| `backend/src/insights/sources/github/` | GraphQL client, queries and normalization (the only GitHub I/O) |
| `backend/src/insights/sync/` | arq jobs: `queue.py` job lifecycle and locks, `jobs.py` sync runs, `store.py` writes, `derive.py` timelines/facts and rederivation |
| `backend/src/insights/db/` | SQLAlchemy models and loaders that turn rows into immutable records |
| `backend/src/insights/analytics/` | Pure computation of the snapshot: timeline, facts, efficiency, time ledger, attribution |
| `backend/src/insights/snapshots/` | Snapshot orchestration, readiness (202 Pending), caching and domain errors |
| `backend/src/insights/narrative/` | Evidence pack, hypothesis scoring, prompt, validator, template fallback, LLM client |
| `backend/src/insights/api/` | FastAPI report routes in `routes/insights.py`, health/repos, params, strict schemas, errors, middleware |
| `backend/migrations/` | Alembic, currently one initial revision |
| `backend/tests/` | Unit and integration tests, factories, golden snapshot (`tests/golden/`) |
| `backend/eval/` | Synthetic scenarios and the narrative evaluation harness |
| `frontend/src/` | React dashboard: components, `format.ts`, `api.ts` request cancellation |
| `docs/` | `REFERENCE.md` contracts, evaluation results and trade-offs; the architecture diagram |

## Commands

Python 3.12 with uv, Node 24, Docker Compose v2. Keep lockfiles; install with
`uv sync --frozen` (backend) and `npm ci` (frontend).

| Command | Purpose |
|---|---|
| `docker compose up --build -d` | Run the stack (dashboard :5173, API docs :8000/docs); needs `.env` from `.env.example` |
| `make lint` / `make fmt` | Ruff + strict mypy / apply formatting and fixes |
| `make test-unit` / `make test` | Unit tests / full suite (integration tests need Docker) |
| `make eval-offline` | 15-case narrative evaluation with the stub LLM |
| `make eval` | Same against real Bedrock; needs the user's credentials, so ask the user to run it |
| `npm test`, `npm run typecheck`, `npm run build` | Frontend checks (in `frontend/`) |

Without Make (Windows), run the equivalent `uv run` commands from the Makefile.

## Invariants

**Boundaries**
- `analytics/` performs no database or HTTP I/O. API requests read local data only; workers
  are the only callers of GitHub.
- `snapshots/`, `narrative/` and `sync/` never import `api.*`.
- Keep `analytics/types.py`, `analytics/facts.py` and `analytics/pointer.py` as separate
  modules; merging them creates import cycles. After moving code, import every module in a
  fresh interpreter to catch cycles.

**Determinism and versions** (constants live in code; do not copy their values into docs)
- Snapshots are pure functions of their inputs: the same data and parameters give the same
  bytes. The golden file pins this.
- Bump `ANALYTICS_VERSION` (`analytics/__init__.py`) whenever snapshot output or derived facts
  change. It is part of the snapshot ID and of the derive key: new requests get new snapshot
  IDs (old snapshots stay stored until retention expires, so caches are isolated, not
  invalidated) and workers
  rederive existing PRs in the background.
- `SAMPLING_SEED_VERSION` (`analytics/stats.py`) freezes bootstrap seeds. Change it only to
  change statistical sampling on purpose.
- Bump `PROMPT_VERSION` (`narrative/prompt.py`) for any prompt or tool-schema change.

**Metric semantics**
- Every section uses the period-active cohort: PRs opened, or with identified human activity,
  in the period. Comparisons apply the same rule to the previous period.
- The time ledger covers the post-ready waiting time of merged PRs only (no bot or backport
  PRs, no pre-ready coding time).
- Wording must match what is measured: PR-hours are elapsed waiting, not effort; a large
  waiting share is not a proven cause; a large change can still be within normal variation.
  Keep the frontend guides and the narrative template consistent.

**Narrative**
- Code computes every number; the LLM only writes wording. Outputs must pass the validator
  (numbers, citations, hedge levels, English-only check); otherwise one repair, then the
  template. Never send PR titles, bodies, comments or user logins to the LLM.

**Data collection and storage**
- GraphQL queries must keep the PR `id` (timeline pagination) and actor `__typename` (bot
  detection), even if those values are not stored.
- Until the first release, schema changes edit the single initial migration and require a
  database rebuild (ask before `down -v`). After release, use forward migrations only.

## Comments

- Every module starts with a one- or two-line docstring: responsibility and boundary.
- Docstrings go on cross-layer entry points and contracts: one summary line, then only
  non-obvious details (units, window semantics, when `None` is returned, determinism).
- Inline comments explain why, not what; do not restate names or types.
- No change history, ticket numbers, authorship or commented-out code. Lines stay within 100
  characters. Update comments when behavior changes; long explanations belong in
  `docs/REFERENCE.md`.

## Verification

- Every change: `make lint`, `make test`, `make eval-offline`, plus frontend checks when
  `frontend/` changes.
- Golden snapshot: regenerate with `UPDATE_GOLDEN=1 uv run pytest tests/unit/test_snapshot.py` (in `backend/`)
  and review the diff; it may contain only the intended changes.
- Prompt changes need a real `make eval`: all gates pass, first-attempt validity at least 0.90
  and numeric, citation and hedge consistency 1.00. Record results in the `docs/REFERENCE.md` testing section.
- UI changes: check 7/30/60-day and custom periods, the pending state, the configuration
  notice, the narrative panel and the time ledger in a browser.
- Update the docs your change affects: README (run and submission notes) and REFERENCE
  (contracts, results and trade-offs).

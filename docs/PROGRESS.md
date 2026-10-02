# Implementation progress

The plan is the requirements source. Milestones are accepted only after their gates pass.

| Milestone | State | Verification |
|---|---|---|
| M0 | Passed | Ruff check/format and strict mypy passed; 16 unit tests passed; Compose build/start, healthz, readyz, OpenAPI title and UID 10001 verified; test volume removed. |
| M1 | Passed | Eleven tables, indexes, defaults and foreign keys verified; upgrade/downgrade/re-upgrade passed in Postgres 16; 18 tests passed; strict mypy/ruff passed; Compose migrate exited 0 and API ready. |
| M2 | Passed locally | 45 unit tests and 46 total tests passed; ruff and strict mypy passed. Pagination, dismissed reviews, bot detection, ETag, retries and sanitized errors covered. Live GitHub smoke pending: GITHUB_TOKEN absent. |
| M3 | Passed locally | 53 tests passed (8 integration); ruff/strict mypy passed. Staged backfill, catch-up, resume, open sweep, idempotency, replacement, lock and arq dedup verified with real Postgres/Redis and mocked GitHub. Live backfill pending credentials. |
| M4 | Passed locally | 99 unit tests and 112 total tests passed (13 integration); ruff and strict mypy passed. All 22 timeline cases plus 200 random invariant sequences; classification, transactional facts, 501-row interrupted rederivation and resumption verified. Live invariants pending GitHub credentials. |
| M5-M12 | Pending | Not yet implemented |

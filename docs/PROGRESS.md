# Implementation progress

The plan is the requirements source. Milestones are accepted only after their gates pass.

| Milestone | State | Verification |
|---|---|---|
| M0 | Passed | Ruff check/format and strict mypy passed; 16 unit tests passed; Compose build/start, healthz, readyz, OpenAPI title and UID 10001 verified; test volume removed. |
| M1 | Passed | Eleven tables, indexes, defaults and foreign keys verified; upgrade/downgrade/re-upgrade passed in Postgres 16; 18 tests passed; strict mypy/ruff passed; Compose migrate exited 0 and API ready. |
| M2 | Passed locally | 45 unit tests and 46 total tests passed; ruff and strict mypy passed. Pagination, dismissed reviews, bot detection, ETag, retries and sanitized errors covered. Live GitHub smoke pending: GITHUB_TOKEN absent. |
| M3-M12 | Pending | Not yet implemented |

# Acceptance evidence

## M7 P0 checkpoint

Local code and container checks passed on 2026-10-02 (Pacific time).
Credentials were absent. Synthetic data is explicitly distinguished below.
Human golden review remains pending; no human approval is implied.

| Items | Result | Evidence |
|---|---|---|
| A1 | Passed | Fresh local clone, empty example credentials, fresh Compose volume: migrate exited 0; API healthy; worker running. |
| A2 | Passed | healthz 200 and readyz 200; stopping Redis produced 503 problem JSON with checks.redis=error; starting it restored 200. |
| A5 | Passed | Real Postgres/Redis integration tests cover all five readiness reasons and queued-job Location. |
| A8 | Passed | Four audience/language combinations returned deterministic templates and 304 on conditional requests. HTTP used the synthetic seed-42 snapshot; integration tests verify persisted model_id=template. |
| A10 | Passed | Live isolated stack returned 202 + Location, then 429 + Retry-After. |
| A11 | Passed | Integration test confirms owner query and a single repository query return identical snapshot bytes. |
| A12 | Passed | Live OpenAPI includes all nine endpoint paths; integration test checks public response models. |
| B1, B5 | Passed | 302 tests passed: 256 unit, 46 integration. Includes timeline cases, random invariants, golden determinism, sample gates, scoring and narrative validation. |
| B6 | Pending human | Independent arithmetic checks passed, but a person still needs to review the golden values. See DECISIONS. |
| C1 | Passed | ruff check, ruff format --check (107 files), strict mypy (68 source files). |
| C2-C4 | Passed | No TODO/FIXME/XXX/NotImplementedError in backend source. Analytics is pure; narrative I/O appears only in service/client. Locked dependencies follow the plan. Frontend is not implemented at this checkpoint. |
| C5-C6 | Passed | One Conventional Commit per completed milestone; decisions include concrete reasons. |
| D1-D2 | Passed | Credential-shaped matches in committed history excluding the supplied plan: 0. .env ignored; example tokens empty. |
| D4-D8 | Passed | Live malformed query 422 and untracked repository 403; tests cover sanitized 500, SSRF prevention, evidence privacy and malicious location names. SQL text calls reviewed: static transaction/health statements and schema defaults only. |
| D9 | Passed | API and worker each returned UID 10001 in the clean container build. |
| E3-E6 | Passed locally | API source-import isolation test passes; batched writes and paginated ingestion reviewed; analytics uses indexed/sorted linkage; snapshot CPU work and SDK calls use threads. Credential-dependent quota/performance evidence remains pending. |
| F2 | Passed | All 29 captured API/worker startup and request log lines in the clean build parsed as JSON; request records include request_id, method, path, status and duration. Application worker events include job/repo/phase. |
| F3 | Passed | With empty GitHub token: API starts, repos reports missing_token, insight returns 503 data-unavailable with repository status. |
| All P0 credential-tagged checks | Pending credentials | A3/A4/A6/A7/A9, B2-B4, D3, E1/E2 and live E4, F4/F5 were not executed against GitHub/Bedrock. |
| M12-tagged checks | Deferred to M12 | Final README, performance report and submission archive. |

The clean-clone run found and fixed two operational issues before acceptance:
parallel services exporting the same backend image tag, and duplicate plain-text arq
logs. API now builds the shared image once, and both CLI entrypoints use the same
JSON formatter. Both fixes have regression or live-container verification.

Seven warnings in the test run come from upstream testcontainers decorators and
the plan-selected pathspec gitwildmatch pattern; no test or type-check failure remains.

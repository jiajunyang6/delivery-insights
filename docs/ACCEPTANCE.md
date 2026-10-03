# Acceptance evidence

Final local acceptance was performed on 2026-10-02 Pacific time.
The identifiers below map one-to-one to the supplied `docs/plan/12-acceptance-checklist.md`.
That supplied file is preserved as the original requirements source.
"Pending credentials" and "Pending human" are explicit exclusions, never implicit passes.
Synthetic results do not establish live GitHub behavior or causal validity. Bedrock was called
with real credentials, using only synthetic evidence; all eight evaluation gates passed.

## Functional checks

| Item | State | Evidence |
|---|---|---|
| A1 | Passed locally | Fresh clone of 8971fdf, empty example credentials and fresh volume: build/start succeeded, migrate exited 0, API healthy, worker running, UI 200. |
| A2 | Passed locally | Real Redis stop produced readiness 503 with checks.redis=error; restart restored 200. Worker restarted automatically; checked two startup records and its running process. |
| A3 | Pending credentials | Live dotnet/runtime staged 7/30/180-day backfill and open sweep. Mocked-source real-database checkpoint tests pass. |
| A4 | Pending credentials | Live 7/30/90-day snapshots and 304. Synthetic 42/90-day HTTP snapshots and ETag behavior pass. |
| A5 | Passed locally | Real Postgres/Redis tests cover all five readiness reasons; browser showed `rederive` and `stale` Pending progress. |
| A6 | Pending credentials | Nine live curl groups require synchronized dotnet/runtime. Equivalent local status/header paths have automated and synthetic HTTP evidence. |
| A7 | Pending credentials | Real-repo drilldown totals pending. Synthetic merged rows=427 and at-risk rows=97 exactly match snapshot totals. |
| A8 | Passed locally | All four synthetic template variants return 200/304 and `llm_disabled`; integration verifies persistent `model_id=template`. HTTP success cache is exactly `private, max-age=3600`. |
| A9 | Pending credentials | Four real Bedrock HTTP variants on a synthetic 42-day snapshot pass, including three evidence-value checks each and 304. The specified dotnet/runtime 30-day narrative and human spot check still require GitHub data. |
| A10 | Passed locally | Isolated stack returned 202 + Location, readable job, then 429 + Retry-After. |
| A11 | Passed locally | Integration and synthetic HTTP confirm one-repo `org=` and `repo=` identities are equal. |
| A12 | Passed locally | Live local `/docs` 200 and OpenAPI contains all nine paths and typed response contracts. |

## Correctness and code quality

| Item | State | Evidence |
|---|---|---|
| B1 | Passed locally | 336 tests: 287 unit, 49 integration. All 22 timeline cases, 200 randomized invariant sequences, shuffled-input byte determinism and golden checks pass. |
| B2 | Pending credentials | Live repository invariants pending. Stored synthetic data: 2,172 PRs, zero violations. |
| B3 | Pending credentials | A person must compare three real PRs against GitHub timestamps; no invented PR numbers or spot-check claims. |
| B4 | Pending credentials | Real ledger reconciliation pending. Synthetic 427-row reconciliation has relative rounding error 0.00000765 (<0.1%). |
| B5 | Passed locally | Quantile/rate/location/driver/cohort sample gates return null with explicit status where required. |
| B6 | Pending human | Review `backend/tests/golden/snapshot_seed42.json` key values. Independent arithmetic is tested, but the agent cannot claim human review. M5/M8/M9/M10 commits explain golden changes. |
| C1 | Passed locally | Ruff check and format: pass (122 files); strict mypy: pass (77 source/eval files). Exact Makefile uv subcommands ran on Windows without GNU make. |
| C2 | Passed locally | Source scan: no TODO/FIXME/XXX/NotImplementedError; no commented-out implementation found; dependencies reviewed against actual imports/build/test use. |
| C3 | Passed locally | Analytics imports no DB/network clients; only narrative service/client perform I/O. Dataset loader location is documented in DECISIONS. |
| C4 | Passed locally | Backend dependencies follow the plan. Vite/plugin-react/Node upgrade addresses the old build-tool advisories; no additional application framework. |
| C5 | Passed locally | Conventional Commit milestones M0–M11 recorded; M12 finalizes this audit and deliverables. |
| C6 | Passed locally | Each DECISIONS row gives a concrete reason; calibration and chain-time deferrals are explicit. |

## Security and performance

| Item | State | Evidence |
|---|---|---|
| D1 | Passed locally | Full `git log -p --all`, excluding supplied plan, has zero matches for the required credential patterns. Final archive procedure repeats the check. |
| D2 | Passed locally | `.env` is ignored; both example token variables are empty. |
| D3 | Pending credentials | After real Bedrock HTTP calls, the literal Bedrock key occurs zero times in captured API/worker logs; GitHub token scan still needs a real GitHub call. Exception regression tests prove untrusted exception text is omitted from JSON logs. |
| D4 | Passed locally | Integration and live local malformed repo/date/cursor return 422; untracked repo returns 403. Error details do not echo raw invalid input. |
| D5 | Passed locally | No request-controlled upstream host; REST rejects absolute URLs; adapter/client tests pass. |
| D6 | Passed locally | Every SQL text call reviewed: static schema defaults, `SELECT 1` or `SET TRANSACTION READ ONLY`; application data uses SQLAlchemy binding. |
| D7 | Passed locally | Evidence/validator tests cover text/user-name exclusion, malicious locations and prompt construction. |
| D8 | Passed locally | Sanitized 500 response tests pass; exception messages/tracebacks are excluded. |
| D9 | Passed locally | Container UIDs: api=10001, worker=10001, web=101. |
| D10 | Passed locally | No dangerous HTML rendering. React text plus strict HTTPS GitHub hostname/userinfo/port checks for external links. |
| E1 | Pending credentials | dotnet/runtime 90-day cold compute threshold unverified. Synthetic cold HTTP: 526.27 ms, 894 merged PRs. |
| E2 | Pending credentials | Real-repo warm p95 unverified. Synthetic 50-request warm HTTP p95: 35.83 ms through nginx. |
| E3 | Passed locally | API import isolation test confirms no `insights.sources` modules loaded. |
| E4 | Passed locally / live pending | Batched upserts/events/files and bounded GraphQL pagination reviewed/tested. Live quota remaining above 1,000 requires GitHub. |
| E5 | Passed locally | Linkage uses keyed lookup; author concurrency uses sorted timestamps/bisect, without pairwise PR scans. |
| E6 | Passed locally | Snapshot/PR-row computation and boto3 use `asyncio.to_thread`. |

## Operations and delivery

| Item | State | Evidence |
|---|---|---|
| F1 | Passed locally / live pending | Final code started from a clean clone using the README Compose/copy commands (PowerShell copy equivalent). Real Bedrock eval passed; GitHub curl/smoke remain pending. Final-commit clean-clone evidence is included in the external delivery report. |
| F2 | Passed locally | All 28 captured API/worker lines parsed as JSON, including the outage; 11 access records contain all required fields. Three exception regressions and worker recovery pass. |
| F3 | Passed locally | Empty token: API/UI start, repos reports `missing_token`, insights return 503 `data-unavailable`. |
| F4 | Pending credentials | Full `make smoke` needs live data; shell syntax and underlying local HTTP contracts are checked. |
| F5 | Pending credentials | Live worker restart/backfill-resume demonstration pending; real-DB mocked-source interrupted phase/rederive tests pass. |
| G1 | Passed locally | README has all 12 required sections within 300–450 lines; commands match Compose/Makefile. Measured numbers are explicitly synthetic/local. |
| G2 | Passed locally | Trade-offs, omissions, completed extras and limitations match implementation; uncalibrated confidence and deferred chain time are explicit. |
| G3 | Prepared by delivery step | The accompanying delivery report records milestone gates, tests/eval, deviations, pending items, packaging checks and limitations. |
| G4 | Pending human finalization | No agent placeholders remain. Three supplied confirm placeholders remain verbatim for the human; no human ownership/review is fabricated. |
| G5 | Passed locally | Four one-more-day items, prioritized and consistent with scope/limitations. |
| G6 | Final packaging step | After final commit, clone without hardlinks, remove local origin/reflogs, repack Git, create tar.gz with `.git`, inspect three package checks and clean worktree. Exact outputs go in the external delivery report. No push/upload. |

## P1 checks

| Item | State | Evidence |
|---|---|---|
| H1 | Passed offline | 20 runs, all gates pass, exit 0. Root-cause=14/16; no-signal abstention=4/4; high precision=14/14; validity/numeric/citation/hedge=20/20; fallback=0/20. |
| H2 | Passed with real Bedrock | Prompt v3: 20 cases; first-attempt 18/20; numeric/citation/hedge 19/19; root-cause 14/16; abstention 4/4; high precision 14/14; fallback 1/20. All gates pass, exit 0. Earlier v1/v2 failures retained. |
| H3 | Passed by agent / human pending | npm ci/typecheck/build pass, npm audit zero; UI/proxied health 200. Browser checked audience, language, citation highlighting, 20→50→97 pagination, date presets and 202 progress. Human visual signoff is pending. |
| H4 | Pending credentials | Live area owner counts and CODEOWNERS-mode rederivation pending. Parser and ownership-invalidation integration tests pass. |
| H5 | Passed locally | CI object/coverage contract, mapping and overlap accounting tested; default incomplete-CI hypothesis cap tested at 0.50. |
| H6 | Passed locally | Drivers, survival, historical predictability are typed and tested; intentional golden update described in M9. |
| H7 | Passed locally | Director/manager × English/Chinese templates and real Bedrock variants pass validators and local HTTP evidence/ETag checks. |

## Interpreting these results

`eval-offline.json` and `eval-bedrock.json` are final prompt-v3 reports; `synthetic-http.json` records
the standalone local performance/reconciliation run. The latter is not a load benchmark.
The quality-tradeoff seed-101 scenario correctly abstains under the unchanged five-event
gate; this accounts for the two root-cause misses. Size-duration coupling is an explicit
synthetic fixture assumption, not an inferred real-world mechanism.

The full suite reports nine upstream deprecation warnings (Testcontainers and pathspec).
Recharts 2 emits a deprecation notice; its current locked npm tree has zero audit findings.
Real Bedrock calls used the user-provided key without committing it. Live GitHub, remote CI,
production deployment, human review and public submission remain unverified. The pending rows above are the handoff checklist.

The real model evaluation uses the same synthetic cases during prompt refinement, not a
held-out set. Its first-attempt rate is exactly the minimum gate; variation on future calls
is expected. The remaining fallback is a chain-citation violation, not a hidden success.
`eval-bedrock-v1.json` and `eval-bedrock-v2.json` retain the failed baselines; `bedrock-http.json`
records final-version endpoint behavior and literal-key log scanning. Root `.env` is preserved.

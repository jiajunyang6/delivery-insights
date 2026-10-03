# Acceptance evidence

## English-only narrative contract (2026-10-03, prompt v6)

The narrative endpoint defaults to English and accepts only `lang=en`. Other values
return 422 problem+json with `errors[].param=lang`. The response keeps `lang: "en"`.
Chinese output examples, templates, action text, hedge keywords, causal/directional
vocabulary and numeric-unit rules have been removed. Frontend requests use the
English default, with no varying-language props or language type.

PROMPT_VERSION is v6. Stored-row and Redis language identities stay fixed to en;
no database migration is needed. Integration tests cover both audiences' default
and explicit en bytes/ETags, OpenAPI restrictions, rejection before generation,
and isolation from v3 bilingual and v4 English caches in Redis and Postgres.
Unit tests reject CJK text across generated fields and retain English confidence,
abstention, downgrade and evidence validation.

Required checks passed: Ruff check/format (123 files), strict mypy (77 files),
312 unit tests, 369 total tests including 57 Docker integrations, all eight
20-case English stub gates, frontend typecheck and production build. Twenty
obsolete positive Chinese tests were removed, five English cases and three
integration cases added. The analytics golden snapshot is unchanged.

The v6 stub suite started at 2026-10-03T20:13:23.231274+00:00; all 20 first attempts passed.
The v6 real Bedrock suite started at 2026-10-03T20:12:19.851387+00:00; 20/20 first attempts
passed, 20/20 final LLM responses passed numeric/citation/hedge checks,
and 0/20 used fallback. Both suites passed all eight gates, with 14/16
root-cause hits, 4/4 no-signal abstentions and 14/14 high-precision hits.
The v4 and v5 trials failed first-attempt validity at 70% and 85%, respectively;
all results remain in [evaluation records](EVALUATION.md). No thresholds were relaxed.

The API and web images were rebuilt locally. Through the nginx proxy at 5173,
lang=zh returned 422; default/en requests returned identical bytes and ETags.
The real manager narrative for s_ce54abc139296b38 at 2026-10-03T20:13:47.008117+00:00
used v6, passed validation after 2 attempt(s), and had
no template fallback. This was an HTTP smoke test, not a new visual browser audit.

## English documentation and submission cleanup (2026-10-03)

All 13 plan chapters and AGENTS are now in English. Section numbering, code-fence
counts and UTF-8 text were checked against the originals; Chinese literals remain
only where they specify bilingual output or validator behavior. All 18 Markdown
documents passed local-link, anchor, table-column and code-fence checks.

The docs directory now contains Markdown only. Twelve JSON/screenshot artifacts,
the redundant PROGRESS journal and the CLAUDE wrapper were removed. Recorded gates
and all 80 per-case outcomes from the four evaluation suites are retained in
[EVALUATION.md](EVALUATION.md); original artifacts are recoverable in Git history.
The README links and packaging anchor were updated. Functional repository files,
including .gitattributes, ignore files, dependency locks, CI, migrations, scripts,
test fixtures and golden data, remain part of the deliverable. Ignored local
environment files, IDE state and dependency/build caches are excluded when packaging.

The complete required gates were rerun after cleanup: Ruff check and format
(123 files), strict mypy (77 files), 327 unit tests, 381 total tests including
54 Docker-backed integrations, all eight gates in the 20-case stub eval, and
frontend typecheck/production build with Node 24.15.0. The stub run at 19:38:06 UTC
retained 14/16 root-cause hits, 4/4 no-signal abstentions and 0/20 fallbacks.
No new real Bedrock, GitHub or browser verification was needed for this documentation
change; their earlier results and limits remain recorded below.

## Pre-delivery review (2026-10-03, analytics 1.4.0)

The original M0-M12 implementation remains complete; this review adds six focused
corrections/optimizations and documents four deferred follow-ups without a scope switch.
Each review commit runs the full required gate set. The final code checks passed:
Ruff check and format (123 files), strict mypy (77 source/eval files), 327 unit tests,
381 total tests including 54 Docker-backed integrations, all eight 20-case stub
evaluation gates, and frontend typecheck/production build.

| Review part | Result | Unit / full tests |
|---|---|---|
| 1: Net review demand, E23, analytics 1.4.0 and refreshed golden | Passed | 310 / 361 |
| 2: Period scope copy and README exclusions | Passed | 310 / 361 |
| 3: V7b exemption only for no output hypotheses | Passed | 313 / 364 |
| 4: NUL cleanup, bad-PR isolation, warning/count/persistence for timeline anomalies | Passed | 323 / 377 |
| 5: Non-JSON errors and stale-page refresh recovery | Passed, including live browser | 323 / 377 |
| 6: Optional cohort memoization and binary activity lookup | Passed | 327 / 381 |

Caching left `snapshot_seed42.json` unchanged, SHA256
`731f3b535cc53c86a37a48ab8c6c8b924ed2a9621a46919961347ae60d79b915`.
The golden test checks generated output against it, not just its file hash.
The stub eval passes validity/numeric/citation/hedge consistency 20/20, root-cause
hits 14/16, no-signal abstention 4/4, high precision 14/14 and fallback 0/20.
See [evaluation records](EVALUATION.md) for per-case outcomes; the checks and
browser steps below retain the review evidence.
Historical M0-M12 counts, model evaluations and timing measurements below describe
their original commits. Human golden/UI review, README confirmations, remote CI,
production release and public submission remain unverified.

### Pagination recovery after sync

Agent-operated browser acceptance against the rebuilt local Compose stack:

1. Opened the 30-day dotnet/runtime manager report (2026-09-04 through 2026-10-03).
   Snapshot `s_ce54abc139296b38` showed 5 of 150 risks; Load more loaded 50 rows.
2. Requested a real manual sync, job `cbdd8ace-1bd7-4143-9d19-725cde124b76`.
   It succeeded at 2026-10-03T17:51:09Z: 2 pages, 50 PR occurrences fetched,
   0 changed PRs, 0 skipped PRs and 0 timeline invariant violations.
3. Load more with the previous cursor returned HTTP 422 (API request
   `7c0466338b50efb95dee32faa7d9a625`). The table cleared all 50 rows and removed
   Load more, showing "Data changed since this report. Refresh the report to load
   matching PRs." and a Refresh report button.
4. Clicking that button loaded snapshot `s_fb42eb48c813c9b2`, reset to 5 of 150,
   and removed the alert. Load more then loaded 50 matching rows without errors.
   The new current-day as_of changed snapshot identity even without changed PR content.

The screenshot files were removed during submission cleanup; the recorded
steps, snapshot IDs and sync job above retain the browser acceptance result.
The production API client also passed four isolated Node checks: HTML HTTP 502,
HTML HTTP 503, structured cursor parameter errors and no-cache propagation.
The same rebuilt stack generated a real English Bedrock manager narrative with
validation passed. The full 20-case Bedrock eval was not rerun for analytics 1.4.0.
This is automated/agent evidence; human UI sign-off remains pending.

## Period-scope update (2026-10-03, analytics 1.3.0)

The user replaced the original all-open scope with PRs created or having identified
human timeline activity during each selected period. Every dashboard section,
narrative example and PR drilldown uses this cohort. Previous-period comparisons
use that period's cohort. Historical thresholds and full PR lifecycle durations
remain references rather than new-PR-only durations. Commit events have no recorded
actor identity, so commits alone do not establish human activity; see README.

- 351 tests passed: 300 unit and 51 database/integration tests. Ruff and strict mypy
  pass; frontend typecheck and production build pass.
- All eight offline evaluation gates pass on the 20 synthetic cases. The earlier
  full Bedrock evaluation below predates this scope change and was not rerun here.
- Live 7-day report: 142 merged, 124 open, 46 at risk; all 283 drilldown rows passed
  an independent read-only SQL activity audit.
- Live 30-day report: 533 merged, 225 open, 144 at risk; all 825 drilldown rows passed
  the same audit. Row ledger totals reconcile within rounding tolerance.
- The seven-day English manager narrative was generated by real Bedrock and passed
  validation after one repair. E25's value and all example URLs match scoped risks.
- Browser: legacy lang=zh is removed; no Narrative selector; English displayed;
  Showing 5 of 46; Load more expands to 46; changing to 30 days resets to 5 of 144.
- Local backfill remains complete at 30 days. This implementation change did not alter the original plan.

The period-scope measurements above retain the verification summary. Older results below
are historical checks of analytics 1.2.0; in particular the old 321-risk total and
language-selector checks no longer describe the current dashboard.


Final local acceptance was performed on 2026-10-02 Pacific time.
The identifiers below map one-to-one to the supplied `docs/plan/12-acceptance-checklist.md`.
That checklist is translated into English with the same requirements and item IDs.
The user limited live backfill to 30 days; original 90/180-day checks are explicitly excluded.
Real dotnet/runtime and Bedrock checks are summarized below. Human review
and causal validity remain unverified; all eight synthetic Bedrock evaluation gates passed.

## Functional checks

| Item | State | Evidence |
|---|---|---|
| A1 | Passed locally | Fresh clone of 8971fdf, empty example credentials and fresh volume: build/start succeeded, migrate exited 0, API healthy, worker running, UI 200. |
| A2 | Passed locally | Real Redis stop produced readiness 503 with checks.redis=error; restart restored 200. Worker restarted automatically; checked two startup records and its running process. |
| A3 | Passed for requested scope | Real 7-day then 30-day checkpoints and open sweep observed. Final target=30, backfill_complete=true, last_sync_status=ok. User stopped the continuing 180-day stage; extra fetched rows retained. |
| A4 | Passed for requested scope | Real 7/30-day snapshots return 200 with all fields, conditional 304 and immutable by-ID bodies. The 90-day request now returns 422 outside the configured 30-day horizon; not claimed as a 90-day pass. |
| A5 | Passed locally and live | Five readiness reasons covered by integration tests. Live initial backfill returned 202/Retry-After/Location; codeowners restart showed rederive readiness before publication. |
| A6 | Passed for requested scope | Live health/ready/repos, 7/30-day insight, snapshot/304, narrative/304, drilldown, manual job/cooldown, org identity and input rejection checked. Original 90-day request excluded. |
| A7 | Passed with real GitHub | All 533 merged rows and 321 at-risk rows reconcile to the 30-day snapshot totals. |
| A8 | Passed locally | All four synthetic template variants return 200/304 and `llm_disabled`; integration verifies persistent `model_id=template`. HTTP success cache is exactly `private, max-age=3600`. |
| A9 | Passed with real GitHub and Bedrock | Four director/manager x en/zh variants return llm, validation=passed and 304, after one repair each. Cited values checked against snapshot refs; e.g. E1 34.17h -> 34h, E6 0.58 -> 58%, E25 -> 321. |
| A10 | Passed locally and live | Real manual sync returned 202 plus readable Location; repeat returned 429 and Retry-After. The resulting 30-day job succeeded. |
| A11 | Passed locally and live | One-repo org=dotnet and repo=dotnet/runtime produce the same real snapshot ID. |
| A12 | Passed locally | Live local `/docs` 200 and OpenAPI contains all nine paths and typed response contracts. |

## Correctness and code quality

| Item | State | Evidence |
|---|---|---|
| B1 | Passed locally | 338 tests: 288 unit, 50 integration. All 22 timeline cases, 200 randomized invariant sequences, shuffled-input byte determinism and golden checks pass. |
| B2 | Passed with real GitHub | Invariants CLI: 3541 PRs, 0 violations after CI/ownership rederivation. |
| B3 | Passed by agent | Compared GitHub pages for #135083, #135101 and #135130 with DB ready/first-human-review/merged times. All nine differences are zero seconds. These were opened ready; no draft transition appears. This is agent verification, not human signoff. |
| B4 | Passed with real GitHub | 533 rows sum to 62070.04h; snapshot=62070.01h. Relative rounding error=0.0000004833, below 0.1%. |
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
| D3 | Passed with real credentials | After actual GitHub sync and Bedrock calls, literal GitHub and Bedrock key matches in captured API/worker logs are both zero. Exception regression tests pass. |
| D4 | Passed locally | Integration and live local malformed repo/date/cursor return 422; untracked repo returns 403. Error details do not echo raw invalid input. |
| D5 | Passed locally | No request-controlled upstream host; REST rejects absolute URLs; adapter/client tests pass. |
| D6 | Passed locally | Every SQL text call reviewed: static schema defaults, `SELECT 1` or `SET TRANSACTION READ ONLY`; application data uses SQLAlchemy binding. |
| D7 | Passed locally | Evidence/validator tests cover text/user-name exclusion, malicious locations and prompt construction. |
| D8 | Passed locally | Sanitized 500 response tests pass; exception messages/tracebacks are excluded. |
| D9 | Passed locally | Container UIDs: api=10001, worker=10001, web=101. |
| D10 | Passed locally | No dangerous HTML rendering. React text plus strict HTTPS GitHub hostname/userinfo/port checks for external links. |
| E1 | 90-day scope excluded | User limited collection to 30 days. Observed 30-day worker cold computation=1376.95ms for 533 merged PRs; no claim of passing the original 90-day gate. |
| E2 | Passed with real GitHub | 30-day cached API HTTP: 50 requests, p95=32.32ms, below 300ms. Local loopback measurement, not a production load test. |
| E3 | Passed locally | API import isolation test confirms no `insights.sources` modules loaded. |
| E4 | Passed locally and live | Batching and bounded pagination tested. Lowest captured GraphQL rate_limit_remaining=4671, above 1000. |
| E5 | Passed locally | Linkage uses keyed lookup; author concurrency uses sorted timestamps/bisect, without pairwise PR scans. |
| E6 | Passed locally | Snapshot/PR-row computation and boto3 use `asyncio.to_thread`. |

## Operations and delivery

| Item | State | Evidence |
|---|---|---|
| F1 | Passed locally and live | Clean-clone startup/Redis recovery tested at 4019880; subsequent fixes built and run in the live Compose stack. Final archive comes from a fresh clean clone. Guarded Quickstart copy preserves .env. No claim of rerunning every old check on each later commit. |
| F2 | Passed locally | All 28 captured API/worker lines parsed as JSON, including the outage; 11 access records contain all required fields. Three exception regressions and worker recovery pass. |
| F3 | Passed locally | Empty token: API/UI start, repos reports `missing_token`, insights return 503 `data-unavailable`. |
| F4 | Passed with real GitHub and Bedrock | Unmodified Python body of scripts/smoke.sh ran on Windows with API=127.0.0.1:8000: all five endpoints 200; narrative generated_by=llm, validation=passed; exit 0. |
| F5 | Passed with real GitHub | Restarted worker during 30-day phase. Same job resumed, start timestamp advanced, saved cursor advanced, rows retained 1787 -> 2032 and 7-day checkpoint was not restarted. |
| G1 | Passed locally | README includes required sections within 300-450 lines. Commands preserve existing credentials; measured real/synthetic values and excluded scope are distinguished. |
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
| H3 | Passed by agent / human pending | npm ci/typecheck/build pass; final nginx image builds and nginx -t passes. Real dashboard/proxy return 200 after API recreation; director/manager and EN/Chinese show validated output. Earlier citation/pagination/pending checks retained. Human visual signoff pending. |
| H4 | Passed with real GitHub | 83 CODEOWNERS plus 134 area-owner rules loaded. 11 displayed area locations have owners_count; missing mappings remain null. Restart in codeowners mode rederived 3541 PRs: 267 merged PRs attributed by rules, 266 directory fallbacks. Restored label mode and verified rederivation. |
| H5 | Passed locally and live | 1171 Actions runs loaded; 30-day CI coverage=36.77%. CI_COMPLETE=false retained; incomplete-CI hypothesis cap tested at 0.50. Azure Pipelines is outside the source. |
| H6 | Passed locally | Drivers, survival, historical predictability are typed and tested; intentional golden update described in M9. |
| H7 | Passed locally and live | All four real dotnet/runtime narrative variants pass validation and 304 after one repair each. Offline/template and synthetic Bedrock tests also pass. |

## Interpreting these results

[Evaluation records](EVALUATION.md) retain the analytics 1.4.0 stub suite and the
earlier analytics 1.2.0 prompt-v3 model suite, with failed v1/v2 baselines.
The standalone local performance/reconciliation measurements are not a load benchmark.
The quality-tradeoff seed-101 scenario correctly abstains under the unchanged five-event
gate; this accounts for the two root-cause misses. Size-duration coupling is an explicit
synthetic fixture assumption, not an inferred real-world mechanism.

The full suite reports nine upstream deprecation warnings (Testcontainers and pathspec).
Recharts 2 emits a deprecation notice; its current locked npm tree has zero audit findings.
Real GitHub and Bedrock calls used restored user credentials without printing or committing them.
Remote CI, production deployment, human review and public submission remain unverified.
90/180-day live checks are excluded by user request, not represented as passes.

The real model evaluation uses the same synthetic cases during prompt refinement, not a
held-out set. Its first-attempt rate is exactly the minimum gate; variation on future calls
is expected. The remaining fallback is a chain-citation violation, not a hidden success.
Synthetic endpoint and real-repository outcomes are summarized in the tables above.
Removed raw reports remain recoverable in Git history.
The user overwrote .env with the example and restored both tokens; guarded setup now avoids this.
The local .env retains the requested 30-day horizon and 7/30-day precompute settings.

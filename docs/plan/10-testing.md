# 10 Testing

## 1. Strategy

Focus tests on error-prone, high-cost behavior: waiting ledgers, temporal attribution, classification, population definitions, LLM validation, HTTP contracts. Eval harness assesses model performance (`08`).

| Layer | Location | Dependencies | Command |
|---|---|---|---|
| Unit | backend/tests/unit/ | No Docker/network/credentials | make test-unit |
| Integration | backend/tests/integration/, @pytest.mark.integration | Testcontainers Postgres 16/Redis 7 | make test |
| Golden | backend/tests/golden/ | Synthetic data, 08 §2 | Included in unit suite |
| Eval | backend/eval/insights_eval/ | Stub or Bedrock | make eval-offline / make eval |

## 2. Conventions and tools

- pytest-asyncio, asyncio_mode=auto; respx.mock(assert_all_mocked=True) for GitHub to forbid real network calls.
- **Inject time**: domain code never directly calls datetime.now(). API get_now dependency; worker/service now parameter. Fix today to synthetic 2026-03-02, satisfying `06` §3.2 date validation.
- Injectable GitHub sleep (`04` §4.1), recording fake without actual waiting.
- FakeLLMClient (`07` §6.4); botocore.stub.Stubber for Bedrock.
- Handwritten GraphQL fixtures in tests/fixtures/github/ matching `04` §3, documenting scenario per file.
- Integration conftest:
  - Session PostgresContainer("postgres:16-alpine"), RedisContainer("redis:7-alpine"), one alembic upgrade head;
  - Before each test TRUNCATE all tables RESTART IDENTITY CASCADE and FLUSHDB;
  - App=create_app() with DB/Redis/get_now/Settings overrides; httpx AsyncClient with ASGITransport and base_url=http://test;
  - seed_synthetic(session,spec,seed): insert synthetic/repo, historical coverage, sweep/sync=as_of, current derived_key, status=ok; generator records in 50-PR pages via save_page; finally link_repo. TRACKED_REPOS=synthetic/repo.

## 3. Unit cases

Each file covers at least these cases; English behavioral names, e.g. test_reply_without_push_returns_to_waiting_reviewer.

**test_config.py**: list whitespace/empty parsing; invalid repos, sync interval not dividing 60, sweep not multiple, out-of-range backfill reject startup; phases [7,30] at target30; SecretStr repr redacts; empty token becomes None; llm_enabled.

**test_logging.py**: one valid JSON line with timestamp/level/event/logger; standard logging same format; third-party loggers WARNING.

**test_health.py**: healthz200; database/Redis readiness failure503 with checks and no original exceptions.

**`test_github_client.py`**:
- Successful GraphQL and rateLimit;
- retry-after waits/retries;
- remaining0 waits reset+5;
- Secondary403 without headers: 60/120/240 backoff, fourth failure GitHubRateLimited;
- 502/timeout: 2/4/8 backoff;
- 401 auth error without retry;
- GraphQL `NOT_FOUND` → `GitHubNotFoundError`;
- Something went wrong halves 25→12→6→5 at same cursor, floor5;
- remaining<200 proactively waits reset+5;
- Accumulated wait>15min raises GitHubRateLimited;
- REST second request If-None-Match;304 cached body;
- Absolute evil.example URL rejected with ValueError;
- No token in success/401 logs or auth exception via caplog; no header logging.

**test_normalize.py**: Bot typename/suffix/built-in/extra lists; discard PENDING; DISMISSED with matching dismissal restores previousReviewState/dismissed=true, otherwise discard; dismissal review_id/author/previous_state; same-second/actor dismissals have distinct node-ID dedup keys and survive; null author Unknown; revert commit parsing; discard non-PR cross refs; deleted accounts; stable dedup/duplicate removal; hash independent of event input order; file truncation; body capped4000.

**test_timeline.py**: all 22 `05` §2.5 cases; point-open boundaries (ready included, end excluded, temporary closure/draft excluded), clipping; 200 seeded legal random sequences including reopen/dismiss/null author satisfy all four invariants.

**test_facts.py**: stage durations/max0; rounds/feedback/post-review commits; post-approval commits+force pushes before merge, zero without approval; second-approval wait; null author safe; no approval merges; review request; size boundaries9/10,99/100,499/500,999/1000; UTC ready weekday/hour; first_response=min(comment,review).

**test_classify.py**: label case-insensitive fallback; last CODEOWNERS match; top-three directories by file count; unclassified; four closed classes/priority superseded over no_review; late rejection; revert title/body/SHA/title fallback; no cross-repo links; unmerged revert flag only; revert-of-revert reland; Reland/Reapply; supersession references/same head; unknown author disables both supersession rules; exclude never-ready; P1 author WIP null if unknown.

**test_stats.py**: quantile sample gates; deterministic seeded bootstrap, significant obvious/no-significant unchanged differences; stable seed_for; P1 KM hand calculation with five samples/two censored, survival and median.

**test_efficiency.py**: hand datasets per metric; rate gates denom29/events4; cohort cutoff min(to_excl,as_of)-N; stalled historical sync gives as_of=last_synced_at/incomplete, excludes later merges/closures. Full-cycle waiting denominator: coding10/reviewer20/author5/merge5 →25/40=0.625; null coding0, closure excluded. Final Cl excludes reopened; never-ready excluded/count reported. Effective throughput subtracts reverted/reverts; no previous fields without comparison; P1 uncovered p85 baseline gives baseline_not_covered and uses §6.2 item8 independently of item1.

**test_bottlenecks.py**: ledger sum equals intervals excluding closure; 1/k location weights; other deduplicates counts across small locations, sums weighted hours/recomputes union medians. Clipped queue weeks/open stock excludes closed. Pareto; what-if formula, missing/zero median yields no item. Risk90d/180d/default p85/p95, own-repo baselines, closed exclusions/as_of clipping; shift threshold. Attribution: state increase shares≤1, zero without growth; location sum=reviewer increase share within1e-4, zero on pure redistribution. Explicit A change3/B−1/reviewer share0.5 →A reviewer increase1/share0.5, B0; sums use unrounded values.

**test_findings.py**: every trigger boundary; <20 merges yields [] and no headline bottleneck/benefit; descending impact/severity/ID; impact_share; four efficiency headline variants with/without finding/what-if; no benefit if first what_if None.

**test_snapshot.py**: golden/determinism (§5); stable/changing snapshot_id/params_hash/versions_hash; completeness; fixed derive_key("label:area-",3) format; rounding; schema validation (`06` §4); every finding ref resolves.

**test_rows.py**: three statuses; historical open excludes closed; own-repo risk baselines/count reconciliation; ledger clipped/excludes closure; current_state; deleted author null.

**test_params.py**: valid/invalid repos a/b,dotnet/runtime,-a/b,a/..,a/b/c,oversized owner,a b/c; repo/org exclusivity, dedup, repo cap; four date rules/defaults; malformed cursors; state/status combinations.

**test_caching.py**: multiple If-None-Match tags/W/*; ETag computation.

**test_evidence.py**: resolving refs, omit nulls; location IDs skip other; spaces/newlines sanitized to location-k, original nowhere in pack; no golden titles/logins; observation order/contradictions; deterministic bytes/hash; M8 ci_complete changes ci_slowdown hash.

**test_hypotheses.py**: `07` §4.5 examples0.78/high,0.63/medium,no mechanism,CI0.5/low,six boundaries; assessable denominator; P0/noCI+risingE1 puts H_ci_bottleneck open/no_data; sides from symptom/mechanism roles, not evidence.side; no_comparison; six alternative branches no_data/insufficient_sample/counter excluded/all mechanisms absent excluded/below_threshold/not_selected; ruled_out only excluded; top-three order.

**test_validator.py**: cover passing and failing examples for every rule:

- Schema, length, language and citation validation.
- Numeric rounding: 41.25 may be reported as 41 h or 41.3 hours, but not 42 h;
  a relative change of 0.1752 as 18% or 17.5%; a share of 0.4213 as 42%.
  Convert 41.25 h to 1.7 days and minutes to hours. Range endpoint 35.1 inherits
  the other endpoint's hours. Keep "3 different areas" unitless; ignore p50/E12;
  parse thousands separators in 1,234.
- V5 rejects values borrowed from uncited evidence and unit mismatches, such as
  reporting count n=61 as 61 hours or a share as 42 hours.
- Reject "fell 18%" or `下降了 18%` for an increasing value; skip direction checks
  when a sentence contains both upward and downward wording.
- Allow global period.days; allow persistence only with evidence-chain citations;
  allow numbers in labels only when that evidence is cited. Validate statements
  sentence by sentence. E5 extra.n_days supports "merged within 3 days [E5]" and
  `在 3 天内合并 [E5]`.
- Select the evidence whose change is reported: if E1/E19 are cited but only E19's
  change is reported, check E19. Reject "fell to 41.2 h" for rising E1. Treat
  "2.4x vs. the rest" as one sentence.
- V7b rejects certainty such as "clearly", `证明了` and `一定会`; allow qualified
  `一定程度上`, `不一定` and `没有证据证明`. With a highest band of medium,
  reject causal "likely" or missing band wording; allow "may".
- If all candidates are low and all output hypotheses are omitted, reject "likely
  main cause"; allow "The signals are not strong enough to support a root cause [E1]."
  V12 rejects "because" without candidates, except in a not-enough sentence.
- Reject unknown hypotheses, omitted high hypotheses, citations outside a chain,
  missing counter-evidence citations, "likely" or `很可能` for medium, downgrades
  that do not lower the band, outside-library hypotheses without significant evidence
  on both sides or without candidates, logins/@handles, and hypotheses or missing
  insufficiency wording when there are no candidates.

**test_template.py**: golden and four non-CI scenarios, plus ci_slowdown after M8, all audience×lang pass. Abstention sentence without candidates; manager≥3 with candidates. Four S1 variants: significant, nonsignificant with change_rel, no previous via coverage=current start (`08` §2.5) with no_comparison, and missing E1; all validate.

**test_llm.py**: Stubber validates Converse modelId/toolChoice/inferenceConfig/system; toolUse parsing; throttle/timeout→LLMUnavailable; logs error code only.

**test_narrative_service.py**: generate only (service cache/locks/persistence in integration API cases). First pass; fail then repaired with error toolResult; double fail→template/failed/persistFalse/violation codes; LLMUnavailable→llm_error; disabled→persistTrue; downgrade0.74/0.5; reorder downgraded high below medium; code alternatives_open; H_llm0.35; evidence only cited; pack_hash canonical first16.

## 4. Integration cases

**test_migrations.py**: all tables/unique constraints/key indexes after upgrade; successful downgrade.

**test_sync.py**: respx GitHub + real Postgres/Redis. Unmarked cases M3; M4/M6/P1 cases added when applicable:
- Staged7→30→BACKFILL_DAYS coverage/resume; first-stage open sweep; stage/end catch-up finalization (`04` §6.3) saves PR updated during pagination and present only in catch-up first page; last_synced_at=catch-up start. Later unfinished backfill runs incremental first. M6 changed-version jobs enqueue precompute;
- Identical resync: same version, no duplicates;
- Changed PR replaces events/files, increments version; M4 rederives intervals/facts without clearing links;
- >100 timeline events complete pagination before persistence;
- Incremental stops watermark−10min;
- Open sweep retrieves old-updated PRs outside coverage;
- Second same-repository job skipped_locked;
- 401 auth_error with short capped token-free job error; no token missing_token;
- enqueue_sync dedup returns existing queued job without new row;
- Invariants CLI zero violations from M4;
- M4 upstream DISMISSED/matching event restores APPROVED/dismissedTrue, case18 intervals; identical resync unchanged;
- M4 identity: first completed stage current key; old fact rows during initial backfill block key publication/enqueue rederive. Startup after location change queues rederive. Failure after first batch retains old repo key; next schedule retries, even without token. Resume skips current rows; completion publishes key/version+1; already-current job no version change;
- M6 housekeeping removes >7d snapshots/cascading narratives/Redis keys and >30d finished jobs, retains newer;
- P1 Actions day>1000 splits four windows, SHA mapping/rederivation; ownership parsing/storage. Changed CODEOWNERS clears repo/all fact keys, enqueues rederive, updates locations; area-only changes version without rederivation.

**test_api.py**: seed_synthetic:
- insight200 ETag/Cache-Control/Content-Location/X-Snapshot-Id, schema valid; second cache hit identical bytes;
- If-None-Match304, no body;
- Pending202 headers/body; all five reasons never_synced/backfill/open_sweep/rederive/stale, rederive Location. missing_token with GitHub-needed reason503; rederive-only still202/job;
- last_synced_at<to_excl →same as_of, incomplete;
- Untracked repo/org403;
- Each invalid parameter422, problem+json/correct param/no raw echo;
- By-ID200 immutable, unknown404, invalid422. Advance get_now8d→snapshot/narrative404 even with Redis keys. Expired same-ID entries in both stores are misses; recompute/refresh created_at then by-ID200;
- PR deleted-author null without500; three statuses/risks count matches snapshot/state/location; concatenated pages complete/no duplicates; old cursor422 after data change;
- Repos includes configured absent DB rows, never;
- Manual202 Location; cooldown429 Retry-After; queued returns existing;
- Job200/404/nonUUID422;
- Rate limit3 gives fourth429 Retry-After; health exempt;
- FakeLLM success200/persistent/cache avoids second call/304; double-invalid200 template/no-store/no narrative row; disabled persistent template model. Missing/deleted-during-write snapshot404, not500. Redis/DB keys include hash matching meta;
- All responses request ID; valid reused, newline/too-long replaced;
- CORS allow header only for configured origins.

## 5. Golden and determinism

- tests/golden/snapshot_seed42.json: baseline seed42 → build_snapshot_from_repo canonical bytes (`08` §2.5); byte comparison prints first differing JSON Pointer/values.
- Regenerate with UPDATE_GOLDEN=1 uv run pytest tests/unit/test_snapshot.py -k golden only for intentional algorithm/threshold changes; explain commit and increment relevant version.
- Identical inputs/reordered records yield identical bytes, verifying explicit sorting.

## 6. Security tests (`test_security.py`, mainly unit; integration as needed)

- Tokens absent from logs/errors/job error on401/timeouts; no header logging/token concatenation. Construct fake token at runtime, e.g. "ghp_"+"x"*36, never full token-shaped source literal to avoid history scan (`12` D1).
- Unhandled500 fixed detail, no original exception.
- location=' OR 1=1 -- returns empty results via bound queries/in-memory filtering, not500.
- No PR title/body/login in evidence/model requests; inspect FakeLLMClient recordings exactly.
- Test API import isolation: `python -c "import insights.main, sys; assert not [m for m in sys.modules if m.startswith('insights.sources')]"`.

## 7. Exclusions

- Frontend styling/interactions, a design trade-off; third-party Recharts/FastAPI/arq internals.
- Real GitHub/Bedrock via credentialed smoke (`04` §8, scripts/smoke.sh)/make eval, outside CI.
- Load testing; M12 performs simple `12` performance checks only.

No coverage-percent gate; every listed case must exist and pass.

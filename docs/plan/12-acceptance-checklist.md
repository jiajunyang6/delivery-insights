# 12 Acceptance checklist

## 0. Usage

- Execute every verification step before checking its box; appearance alone is not acceptance.
- Tags: P0 required, P1 extra, Credentials needs real GITHUB_TOKEN and/or AWS_BEARER_TOKEN_BEDROCK (if absent, mark pending human verification in final report), M12 final-only README/report checks.
- **M7 P0 audit**: all P0 without M12. **M12**: every item.
- Commands run at repository root; API=http://localhost:8000; TO/FROM per `06` §8.

## A. Functionality

- [ ] **A1** `[P0]` Clean clone: cp .env.example .env → docker compose up --build -d; migrate exits0, api healthy, worker running. Inspect docker compose ps -a (without -a hides exited migration).
- [ ] **A2** `[P0]` healthz {status:ok}, readyz200; docker compose stop redis →readyz503 with checks.redis=error; docker compose start redis restores readiness.
- [ ] **A3** `[P0][Credentials]` dotnet/runtime backfill: repo covered_since advances 7→30→120 days, first-stage open sweep timestamp, sync status ok.
- [ ] **A4** `[P0][Credentials]` 7/30/90-day insights200 with headline/efficiency/ledger/bottlenecks/analysis/risks/waste/rework/guardrail/trend/meta; If-None-Match304.
- [ ] **A5** `[P0]` Unready202 Retry-After/Pending, all reasons never_synced/backfill/open_sweep/rederive/stale in integration; credentialed initial-backfill90-day request confirms.
- [ ] **A6** `[P0][Credentials]` All nine curl groups in `06` §8 match descriptions.
- [ ] **A7** `[P0][Credentials]` Risk detail total equals snapshot risk summary; merged total equals meta.sample.merged_prs.
- [ ] **A8** `[P0]` No Bedrock key: narrative200/template/llm_disabled, persisted model_id=template.
- [ ] **A9** `[P0][Credentials]` Real30-day narrative llm/passed; spot-check three numbers via evidence refs into snapshot.
- [ ] **A10** `[P0]` Manual sync202 Location; cooldown repeat429 Retry-After.
- [ ] **A11** `[P0]` org=dotnet and repo=dotnet/runtime yield identical snapshot ID if only this repo tracked.
- [ ] **A12** `[P0]` /docs contains all `06` §1 endpoints/full models.

## B. Correctness

- [ ] **B1** `[P0]` make test passes all22 timeline cases/golden/determinism including shuffled inputs.
- [ ] **B2** `[P0][Credentials]` docker compose exec api python -m insights.sync.invariants --repo dotnet/runtime: zero violations.
- [ ] **B3** `[P0][Credentials]` Three merged PRs: compare ready/first-human-review/merge to GitHub within one minute; report numbers/results.
- [ ] **B4** `[P0][Credentials]` Ledger total equals summed all merged detail ledger_hours, relative rounding error<0.1%. Temporary paginated audit script, not committed.
- [ ] **B5** `[P0]` Insufficient metrics null/insufficient_sample, unit covered.
- [ ] **B6** `[P0]` Human key-value review of first golden (`01` M5); commit explains.

## C. Code quality

- [ ] **C1** `[P0]` make lint passes ruff check/format, strict mypy.
- [ ] **C2** `[P0]` `grep -rnE "TODO|FIXME|XXX|NotImplementedError" backend/src frontend/src` has no results; no commented-out code/unused dependencies.
- [ ] **C3** `[P0]` Pure boundaries: `grep -rnE "^(from|import) (insights\.db|insights\.redis|httpx|boto3|sqlalchemy|redis)" backend/src/insights/analytics backend/src/insights/narrative` hits only analytics/dataset.py, narrative/service.py, narrative/llm.py.
- [ ] **C4** `[P0]` Dependencies match `02` §2; justify extras in DECISIONS.
- [ ] **C5** `[P0]` Conventional Commit per milestone, git log --oneline.
- [ ] **C6** `[P0]` Every DECISIONS row has reason.

## D. Security

- [ ] **D1** `[P0]` No history tokens: `git log -p --all -- . ':(exclude)docs/plan' | grep -nE 'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AWS_BEARER_TOKEN_BEDROCK=[A-Za-z0-9]'` yields none. Exclude plan's explanatory patterns. Fake test tokens constructed at runtime, e.g. "ghp_"+"x"*36, never complete source literals.
- [ ] **D2** `[P0]` git check-ignore -q .env && echo ignored prints ignored; example tokens blank.
- [ ] **D3** `[P0][Credentials]` After sync/narrative, `docker compose logs api worker | grep -cF "$(grep '^GITHUB_TOKEN=' .env | cut -d= -f2-)"` prints0; same for Bedrock key.
- [ ] **D4** `[P0]` Invalid repo/date/cursor422, untracked403, no raw input echo; tests/curl.
- [ ] **D5** `[P0]` No SSRF: no URL/host request input, GitHub addresses only Settings, full REST URLs rejected.
- [ ] **D6** `[P0]` SQL parameterized; inspect every grep -rn "text(" backend/src match for bound parameters.
- [ ] **D7** `[P0]` No titles/bodies/logins in pack/requests; location sanitization tested.
- [ ] **D8** `[P0]` 500 fixed text, no trace/exception.
- [ ] **D9** `[P0]` docker compose exec api id -u and worker id -u print10001; P1 web prints101.
- [ ] **D10** `[P1]` No dangerouslySetInnerHTML; only https://github.com/ clickable links.

## E. Performance

- [ ] **E1** `[P0][Credentials]` dotnet/runtime90-day cold snapshot<3s; worker precompute snapshot_computed log with duration_ms/load_ms/compute_ms/merged_prs.
- [ ] **E2** `[P0][Credentials]` Cached insight p95<300ms:

  ```bash
  for i in $(seq 50); do curl -s -o /dev/null -w '%{time_total}\n' "$API/v1/insights/delivery?repo=dotnet/runtime&from=$FROM&to=$TO"; done | sort -n | sed -n '48p'
  ```

- [ ] **E3** `[P0]` No request-path GitHub; import isolation test passes.
- [ ] **E4** `[P0]` Bulk writes/GraphQL pagination, no per-row INSERT loop; credentialed backfill quota remains>1000.
- [ ] **E5** `[P0]` No analytics O(n²) PR loops; author concurrency uses sorted sweep.
- [ ] **E6** `[P0]` CPU snapshots/boto3 via asyncio.to_thread.

## F. Operations

- [ ] **F1** `[P0][M12]` Clean-clone README quickstart succeeds verbatim.
- [ ] **F2** `[P0]` JSON logs; API request_id/method/path/status/duration_ms; worker job/repo/phase.
- [ ] **F3** `[P0]` Without GitHub token API starts, repos missing_token, insights503 data-unavailable with reason.
- [ ] **F4** `[P0][Credentials]` make smoke passes.
- [ ] **F5** `[P0][Credentials]` Restart worker resumes backfill cursor without refetching completed stages; inspect jobs/logs.

## G. Judgment and documentation

- [ ] **G1** `[P0][M12]` All `11` §2 sections, no unverified numbers, commands match Makefile/Compose.
- [ ] **G2** `[P0][M12]` Trade-offs/Not done/Beyond brief/Known limitations match implementation; no unfinished extras claimed. Confidence disclosed as deterministic/uncalibrated (`11` §2 item6/§7.1); real-data calibration/chain timing deferred in Not done.
- [ ] **G3** `[P0][M12]` Final report per AGENTS §7.
- [ ] **G4** `[P0][M12]` AI assistance per `11` §7.5; agent placeholders replaced only with confirmed tools/model/work/checks/limits; grep '<agent:' has no matches. Human confirm placeholders retained verbatim, listed in pending report; no invented human decisions/reviews.
- [ ] **G5** `[P0][M12]` One more day: ≤5 impact-ranked remaining items, consistent with omissions/limitations, no completed items.
- [ ] **G6** `[P0][M12]` Last-step submission audit (`11` §9): empty worktree. Fresh-clone ../delivery-insights.tar.gz includes .git; checks has .git/no local files/no local path, excluding .env/node_modules/.venv/local paths; absolute archive path in report. Public alternative human pushes then unauthenticated git ls-remote succeeds. Agent never pushes/uploads/sends (AGENTS §8).

## H. P1 extras

- [ ] **H1** `[P1]` Offline eval all `08` §6 gates, exit0.
- [ ] **H2** `[P1][Credentials]` Real eval all gates; report metric table.
- [ ] **H3** `[P1]` npm ci/typecheck/build pass; proxy healthz ok. Manual: audience changes sections, clickable citation highlights,202 progress.
- [ ] **H4** `[P1][Credentials]` Area owners_count present; codeowners mode uses rules after restart/rederivation.
- [ ] **H5** `[P1]` CI section/coverage present; default incomplete-CI hypothesis≤0.5.
- [ ] **H6** `[P1]` Drivers/survival/predictability present; golden update explained in commit.
- [ ] **H7** `[P1]` Both English director/manager variants validate in templates/eval; non-English lang requests return 422.

# Refactor verification

Branch: `optimization-v2`. Baseline: `pre-refactor` (`58d7b3d`). Working plan is intentionally
untracked. Each stage is committed after lint, full integration/unit tests, offline evaluation,
frontend tests/typecheck/build and the external baseline comparison pass.

## Stages 0–7

- Baseline: 400 backend tests; 71 source Python files and 10,522 lines. After stages 1–6:
  402 tests (331 unit, 71 integration), 71 source files and 10,262 lines. Line reduction is
  260; the original reduction estimate is not a completion gate.
- Pure refactors compare golden seed 42, five scenarios × seeds 101/202, and an additional
  CODEOWNERS/area-owner fixture, for director and manager. The external comparison script
  checks snapshots, evidence, candidates/scores, templates, assembly, validator codes and
  prompt messages. It does not ignore metadata or regenerate expectations blindly.
- Allowed changes are removed unused fields and recomputed versioned snapshot/link/hash
  identities. Bootstrap seed integers remain identical to the 1.4.0 canonical hash; a
  near-significance-boundary test preserves exact interval endpoints and significance.
- Integration coverage checks PostgreSQL/Redis/HTTP identity isolation, metadata reuse with
  ownership counts, idempotent files/events, stale pagination and all narrative cache/lock/
  repair/timeout/foreign-key/language paths. Generation extracts evidence once; repair has
  only the remaining total deadline. API service imports remain independent of adapters.
- In stage 3, every one of the 71 source modules was imported in its own fresh Python process.
- Stage 4 uses the plan's permitted first-three scope: linking indexes/reverts/classification,
  event counting, and timeline dispatch. Other complexity rewrites remain optional/deferred.
- Browser verification on the current Vite source used an isolated synthetic HTTP fixture
  server, without writing to collected data: both views, 7/30/60-day presets, 5 → 12 risk rows,
  populated owner counts, hypothesis cards, citation focus, all three abstention messages,
  Where to look first, and pending backfill. Console warnings/errors were empty.
  [Screenshot](screenshots/refactor-narrative.jpg) shows citation focus in the refactored UI.
- Ruff check/format, strict mypy, all 402 backend tests, all 8 frontend tests, typecheck/build
  and all eight offline gates pass. Offline first-valid/numeric/citation/hedge/abstention/
  high-precision rates are 1.00, root-cause hit rate 0.875, fallback rate 0.00.

## Stage 8

Final prompt v8 passes all eight real Bedrock and offline gates with unchanged validators
and tool schema. First-valid/numeric/citation/hedge/abstention/high-precision rates are
1.00, root-cause hit rate is 0.875, and fallback is 0.00. The final real run has 20/20
first-valid responses; rejected candidates A/B and the pre-alignment candidate C run
are retained in [EVALUATION.md](EVALUATION.md). These same synthetic cases were used
for prompt refinement, not held-out validation. The stage-8 baseline comparison excludes
only changed prompt messages; business values, evidence, templates and validator codes
remain equal. All 402 backend and 8 frontend tests, lint/type checks and build pass.

After stage 8: 71 backend Python files, 10,249 lines (273 fewer than baseline).

## Stage 9: storage consolidation and confirmed live rebuild

The user explicitly instructed execution on `optimization-v2` without merging into main.
The branch prerequisite is overridden. After explicit current-time confirmation, the local
database and cache were deleted with `docker compose down -v`; main remains at `58d7b3d`.

- Revisions 0001–0003 are consolidated into `0001_initial`, including `links_pending`.
  The two legacy-upgrade cases are removed; interrupted linking, retries and merge changes
  remain covered. Migration tests verify complete metadata, upgrade, downgrade and re-upgrade
  on an isolated PostgreSQL 16 container.
- Removed PR fields: `source_id`, `author_type`, `merged_by`, `files_truncated`, `changed_files`.
  Removed audit/duplicate metadata: PR `synced_at`, ownership `fetched_at`, facts `computed_at`,
  snapshot `data_versions`. Repository `last_synced_at` and versioned payload metadata remain.
- Removed unused facts and their producers: `first_response_at`, `force_pushes_after_first_review`,
  `is_reland`, `superseded_by_pr_id`, `author_open_prs_at_ready`, `ready_weekday`, `ready_hour`.
  Grep found only producers, persistence plumbing and tests; retained analytics consumers do
  not read them. Reland target IDs, close classes, revert links and approval/update counts remain.
- Removed only `ix_intervals_pr`; the existing unique `(pr_id, seq)` index covers PR lookups.
  All other indexes remain; representative before/after EXPLAIN was not run.
- A new regression checks all 205 events across three pages for human and Bot actors. It
  checks upstream PR IDs, both cursor requests and the retained actor `__typename` query.
  Hashes now exclude removed domain fields; repeated-save and stale-page tests preserve
  retained PRs, events, files, facts and intervals without rewriting unchanged records.
- All 402 backend tests (333 unit, 69 integration), Ruff check/format, strict mypy, the
  business/evidence/template/validator baseline comparison, all eight offline gates, and
  all 8 frontend tests/typecheck/build pass. Backend/frontend Compose images also build.
  Offline report: `backend/reports/eval-20261004T041216Z-stub.json` (ignored local artifact).
- After stage 9 code changes: 71 backend source files, 10,176 lines, down another 73 lines
  from stage 8 and 346 lines from `pre-refactor`. Migration consolidation removes a further
  59 lines outside `backend/src`. Documentation and regression coverage are counted separately.
- A preliminary non-elevated unit run had two temporary-directory permission errors;
  the complete elevated run passed all cases. This was an execution-permission limitation,
  not a code assertion failure.

Before reset, the local database held 3 repositories, 6,423 PRs, 186 snapshots and 91 narratives.
The rebuilt empty database had zero PRs and revision `0001_initial`; migrate exited normally,
the API/database/cache became healthy, worker/web started and `/readyz` returned ready.
Bevy completed successive 7/30/120-day coverage checkpoints, ending at
`2026-06-06T04:17:00Z` with `last_sync_status=ok`: 48 pages, 32,541 events, 2,384 fetches,
1,786 changed fetches and 1,640 distinct PRs. Both invariant violations and skipped PRs were
zero. Repeated PR fetches across checkpoints explain the differing fetch and row counts.

The HTTP audit repeated after CI/ownership/incremental completion checked periods ending
2026-10-03: 7/30/60-day snapshots returned
200, conditional ETags returned 304, strict response schemas passed and all six English
narratives passed final LLM validation. The 30-day manager response required one repair.
Browser checks covered both views, all three presets, Owners columns, hypothesis cards,
citation highlighting, 5 → 50 → 64 risk rows and stale-report recovery during backfill.
The final enriched 30-day report also expanded 5 → 50 → 63 matching risk rows.
Console warnings/errors were empty. [Live overview](screenshots/storage-rebuild-overview.png)
and [citation focus](screenshots/storage-rebuild.png) use the rebuilt collected data.

For the browser's period ending 2026-10-04, both 60-day audiences fell back to templates
after two invalid model attempts: director `V8:invalid_downgrade`/`V7:hedge_mismatch`, manager
`V7:hedge_mismatch`. The UI remained usable; this does not establish universal live LLM
validity and is separate from the passing stage-8 synthetic Bedrock gates.
CI enrichment fetched and saved 17,658 runs, rederived 1,090 PRs and had zero invariant
violations. Ownership completed successfully with zero parsed rules; every location retains
`owners_count`, with null/“—” representing unavailable source ownership. Populated owner
counts are covered by the separate synthetic and integration fixtures.
The follow-up incremental job fetched 100 PRs across two pages: `prs_changed=0`, zero skipped
PRs and zero invariant violations. All 1,640 stored PR hashes and upstream update timestamps
matched the pre-incremental capture, with no added or removed rows.
[Sanitized live evidence](storage-rebuild-verification.json) records checkpoints, job statistics,
schema/index checks, hash comparisons and HTTP/narrative outcomes. Live storage acceptance is
complete; the observed model fallbacks remain an explicit narrative-quality limitation.
Remote CI and human numeric/causal review remain pending.

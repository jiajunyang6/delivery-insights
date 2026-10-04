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

Final backend source: 71 Python files, 10,249 lines (273 fewer than baseline).

## Remaining stage 9 prerequisite

Storage stage 9 requires stages 0–8 merged into main and explicit confirmation at the time
of `docker compose down -v`. Existing migrations, storage columns and collected volumes
remain intact until then. Empty initialization, staged 7/30/120-day resync, zero invariant
violations, skipped-PR accounting, stable incremental hashes and live dashboard checks are
not established by the tests above. Remote CI and human numeric/causal review are pending.

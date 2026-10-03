# 08 Eval harness (P1, M10; generator, pipeline, scenarios built in M5)

## 1. Purpose

Evaluate Endpoint 2 using synthetic scenarios with known answers: grounded numbers, valid citations, recovery of planted causes, abstention without signals, and reliable high bands. Run before changing prompts/library/thresholds/model.

- make eval-offline: StubLLMClient (§8), no credentials, CI-suitable; tests deterministic pack/scoring/validator/template.
- make eval: real Bedrock, requires AWS_BEARER_TOKEN_BEDROCK; tests model behavior.

No DB/Redis/GitHub: synthetic data → pure derivation → snapshot → narrative.service.generate (`07` §9.1).

## 2. Synthetic generator (`insights_eval/generator.py`)

### 2.1 Interface

```python
@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    name: str
    pickup_mult: Mapping[str, float]          # Current-period first-review wait multipliers by area
    arrival_mult: Mapping[str, float]         # Current-period arrival-rate multipliers by area
    area_reviewers: Mapping[str, int]         # Current-period available reviewers per area, default 4
    size_mult: float                          # Current-period median PR size multiplier
    first_approval_bonus: float               # Current-period first-review approval probability increment
    rubber_stamp_large_share: float           # Current-period large PRs approved within 10 minutes without comments
    revert_rate: tuple[float, float]          # Previous/current revert probabilities
    ci_enabled: bool
    ci_queue_mult: float                      # Current-period CI queue duration multiplier
    ci_run_mult: float                        # Current-period CI run duration multiplier
    flaky_rate: tuple[float, float]           # Previous/current
    reviewers_wait_for_ci: bool

    @classmethod
    def baseline(cls) -> "ScenarioSpec": ...  # Multipliers 1, increments 0, revert (0.02,0.02), ci_enabled False

@dataclass(frozen=True, slots=True)
class SyntheticRepo:
    repo: str                                 # "synthetic/repo"
    default_branch: str                       # "main"
    records: tuple[PullRequestRecord, ...]
    ci_runs: tuple[CiRun, ...]
    period_from: date
    period_to: date
    as_of: datetime

def generate(spec: ScenarioSpec, seed: int) -> SyntheticRepo
```

- Only randomness: numpy.random.default_rng(seed). Generate in fixed day→PR order; same seed yields identical data.
- Domain objects (`04` §2.1); shared normalization dedup builder, stable=f"syn-{pr_number}-{event_index}" (`04` §5.4); review_id is that review stable value, also used by dismissal references.
- Lognormal LN(m,s)=m*exp(s*z), z~N(0,1), hours unless specified.

### 2.2 Fixed calendar (independent of current date)

| Name | UTC range, inclusive dates |
|---|---|
| History for risk baseline | 2025-09-08 → 2025-12-07 |
| Previous | 2025-12-08 → 2026-01-18, six weeks starting Monday |
| Current | 2026-01-19 → 2026-03-01, six weeks starting Monday |
| as_of | 2026-03-02T00:00:00Z, midnight after current to |

### 2.3 People and locations

- area-A…area-E weights 0.30/0.25/0.20/0.15/0.10. Labels: 90% one, 5% two, 5% none; paths still match area for directory fallback.
- 1–5 files, src/{A..E}/file{k}.cs.
- Internal dev01–dev40 (MEMBER); external ext01–ext30 (CONTRIBUTOR), 20%.
- Four reviewers per area (rev-a1…rev-a4 etc.), cross-area rev-x1…rev-x4 handling 20% of reviews.
- 5% dependabot[bot] (Bot, one approval then merge); 3% release/9.0 backports.

### 2.4 Per-PR generation

1. **Arrivals**: weekdays Poisson(15×arrival_mult[area]), weekends Poisson(5); uniform creation time during day; numbers from 1000; title Change {number} in {area}.
2. **Size**: max(1,round(LN(80,1.1)×size_mult)); additions=round(0.7×size), deletions=rest; changed_files=max(1,size//40).
3. **Draft/ready**: 15% draft, ready=created+LN(10,0.8), emit ready_for_review; first authored_at=created-LN(4,1.0). Non-draft ready=created, authored_at=created-LN(12,1.0). committed_at=authored_at+LN(0.5,0.5), bounded below by created.
4. **Request review**: 70% author requests one reviewer at ready.
5. **First review**: ready+LN(6×area_mult×pickup_mult[area],1.0); baseline area multipliers A1.0/B1.2/C0.9/D1.1/E1.0. 80% from available area_reviewers, 20% cross-area; review_capacity area-B only local. If reviewers_wait_for_ci, max(first_review,latest CI end+LN(0.5,0.5)).
6. **First decision**: approval probability <100 lines 0.55, 100–499 0.35, ≥500 0.20, plus bonus capped 0.95. Otherwise 60% CHANGES_REQUESTED/40% COMMENTED. Current large PR rubber_stamp_large_share instead approves ready+LN(0.1,0.3) hours with no comments.
7. **Rework**: after non-approval, author responds after LN(8,1.0), 80% commit/20% comment; review after LN(5,1.0). kth rereview approval min(0.95,0.6+0.1k); max five rounds, fifth always approves.
8. **Second approver**: 30% receive different reviewer approval after LN(4,1.0).
9. **Merge**: LN(3,1.0) after approval, merged event/by=last reviewer; 10% post-approval premerge rebase commit.
10. **Outcome**: 85% merged; 10% closed unmerged: 30% no-review author closure after ready+LN(120,0.6), 30% reviewer closure after changes requested, 25% author closure while author-waiting, 15% superseded (author closes, new same-author PR created within one day either side and eventually merged; cross_referenced belongs to **old PR**, source=new). Remaining 5% stop in a waiting state, open at as_of.
11. **Revert/reland**: merged PR reverted with probability by merge period. Another developer creates Revert "{original_title}" after LN(30,0.8), body Reverts synthetic/repo#{original_number}, approved/merged within three hours. Half get Reland "{original_title}" after LN(72,0.5), normal flow.
12. **CI** if enabled: run one minute after ready for latest commit; each later commit gets run committed_at+one minute. Queue LN(10/60,0.8)×ci_queue_mult, run LN(1.0,0.5)×ci_run_mult. flaky_rate reruns set attempt=2/success and add a run duration to updated_at. head_sha=commit OID, pr_numbers=(number,). Multipliers apply only to current-created runs.
13. Discard generated events after as_of, leaving those PRs open at observation.

### 2.5 Records to snapshot (`insights_eval/pipeline.py`)

```python
def build_snapshot_from_repo(
    syn: SyntheticRepo, *, location_dimension: str = "label:area-", covered_since: datetime | None = None,
) -> dict[str, Any]
```

Same pure order as production: timeline.build_timeline → facts.compute_facts → classify.link_prs → analytics.dataset.Dataset (same loader return type; data_version=1, covered_since=argument or historical start, last_synced_at=as_of, status=ok) → analytics.snapshot.build_snapshot. Passing current start as coverage makes comparison unavailable without filtering records, for no-comparison template tests. Return parsed snapshot; M5 golden uses baseline seed 42.

## 3. Scenarios (`insights_eval/scenarios.py`, M5)

Unlisted fields use baseline. Define with generator in M5; M7 templates need four non-CI cases. ci_slowdown enters tests/eval after M8 derivation.

| Scenario | Current changes | Expected |
|---|---|---|
| review_capacity | pickup_mult={area-B:4.0}; arrival_mult={area-B:1.4}; area_reviewers={area-B:1} | Top H_review_capacity, location=area-B |
| ci_slowdown | ci_enabled=True both periods; queue_mult=6.0; run_mult=2.0; flaky_rate=(0.03,0.15); reviewers_wait_for_ci=True both | Top H_ci_bottleneck; eval ci_complete=True |
| pr_size_growth | size_mult=3.0 | Top H_pr_size_growth |
| quality_tradeoff | All area pickup_mult=0.4; first_approval_bonus=0.35; rubber_stamp_large_share=0.5; revert_rate=(0.02,0.09) | Top H_quality_tradeoff |
| no_signal | No changes | No hypotheses, abstained=true |

Plant strong effects. If offline gates fail, verify generator against this section and predicates against `07` §3 first. **Do not** change 07 thresholds just to pass. Record genuinely required adjustments in DECISIONS.

## 4. Runner (`insights_eval/run.py`)

```bash
python -m insights_eval.run --llm {stub,bedrock} [--seeds 101,202] [--scenarios review_capacity,...] [--out reports]
```

- Default seeds 101/202, reserving 42 for golden; all five scenarios; each scenario×seed runs director/en and manager/en, 20 total.
- Each: generate → build_snapshot_from_repo → narrative.service.generate with ci_complete=True and fixed now.
- --llm bedrock uses configured region/model; missing key prints AWS_BEARER_TOKEN_BEDROCK is not set, exits 2. Serial, no concurrency.
- --llm stub uses StubLLMClient.
- --out defaults backend/reports, or reports/ relative to backend; ignored by Git/Docker.

## 5. Metrics (`insights_eval/metrics.py`)

| Metric | Definition |
|---|---|
| first_attempt_valid_rate | Share of LLM-invoking runs whose first output validates |
| numeric_consistency | Generated LLM final responses passing rerun V5, sentence-local evidence/unit/direction (`07` §7) |
| citation_validity | LLM final responses passing rerun V4/V6 |
| hedge_consistency | LLM final responses passing V7/V7b/V12 at final bands |
| root_cause_hit_rate | Expected-scenario top final reordered hypothesis (`07` §9.3) matches expected ID; review_capacity also expected location |
| abstention_rate | no_signal runs abstained=true with empty hypotheses |
| high_precision | High-band hypotheses matching scenario's expected ID; any no_signal high is wrong; null if no high |
| fallback_rate | Share generated_by=template |
| Calibration table, reporting only | Count/hits/rate per band; targets high≥80%, medium≥60%, low≥40%, per design |

Calibration reflects **synthetic**, known-answer, strong-effect scenarios only, not real repositories. Confidence is deterministic evidence strength, not probability. Historical backtesting/human-label calibration are deferred; record DECISIONS and README trade-offs/Not done (`11` §7.1/§7.2).

Rerunning means validate assembled final response, not raw LLM output, against the same pack to catch assembly errors.

## 6. Gates

| Metric | Gate |
|---|---|
| `first_attempt_valid_rate` | ≥ 0.90 |
| `numeric_consistency` | = 1.00 |
| `citation_validity` | = 1.00 |
| `hedge_consistency` | = 1.00 |
| `root_cause_hit_rate` | ≥ 0.80 |
| `abstention_rate` | ≥ 0.80 |
| high_precision | ≥0.80; null passes with warning |
| `fallback_rate` | ≤ 0.10 |

Any failed gate exits 1. Stub uses same gates; templates necessarily validate, so first two should be 1.0.

## 7. Reports and comparison

- Write reports/eval-{YYYYMMDDTHHMMSSZ}-{llm}.json with started_at,llm,model,prompt_version,analytics_version,seeds,runs[] (scenario,seed,audience,lang,expected,generated_by,validation,attempts,fallback_reason,violations,top_hypothesis,top_level,abstained,hit,duration_ms,input_tokens,output_tokens),metrics,gates (value,threshold,passed),passed.
- Console prints one row per run and metric table; if earlier same-llm report exists, print metric changes.
- No sensitive information beyond narrative text (synthetic data is not sensitive); optionally runs[].narrative for human checks.

## 8. `StubLLMClient`(`insights_eval/stub_llm.py`)

Implement LLMClient (`07` §6.1), model_id=stub. submit parses pack JSON from user message, calls narrative.template for text/statements, returns tool-input narrative/hypothesis id/statement. Covers full pack→validation→assembly offline path deterministically.

# 05 Analytics algorithms

This file defines all computations. Except dataset.py, functions are pure: immutable input, new output, no I/O. Durations use hours (float); timestamps use UTC.

## 1. Thresholds (`insights/analytics/thresholds.py`)

```python
THRESHOLDS_VERSION = "1.0.0"

MIN_SAMPLES_P50 = 20
MIN_SAMPLES_P90 = 30
MIN_SAMPLES_LOCATION_P50 = 10
MIN_SAMPLES_WEEKLY_P50 = 5
MIN_RATE_DENOMINATOR = 30
MIN_RATE_EVENTS = 5
CHANGE_MIN_RELATIVE = 0.10
BOOTSTRAP_ITERATIONS = 1000
BOOTSTRAP_CI = 0.90

N_DAYS_MERGED = 3
MIN_LOCATION_PRS = 10
MAX_LOCATIONS_IN_SNAPSHOT = 15
DIRECTORY_LOCATIONS_PER_PR = 3

AT_RISK_WARNING_PERCENTILE = 85
AT_RISK_CRITICAL_PERCENTILE = 95
AT_RISK_BASELINE_DAYS = 90
AT_RISK_FALLBACK_DAYS = 180
AT_RISK_MIN_BASELINE = 30
AT_RISK_DEFAULT_HOURS = {"waiting_reviewer": 48.0, "waiting_author": 72.0, "waiting_ci": 6.0, "waiting_merge": 24.0}
AT_RISK_MAX_ITEMS = 20

WHAT_IF_TARGET_HOURS = {"pickup": 8.0, "merge": 8.0, "ci": 2.0}   # CI enabled in P1
REVIEW_CONCENTRATION_TOP_K = 2
LARGE_PR_LINES = 500
FAST_APPROVAL_MINUTES = 10
FAST_APPROVAL_MIN_LINES = 300
LATE_REJECTION_DAYS = 14
LATE_REJECTION_ROUNDS = 2
SUPERSEDE_WINDOW_DAYS = 14
SIZE_BUCKETS = ((10, "XS"), (100, "S"), (500, "M"), (1000, "L"))   # Otherwise "XL"
CI_COVERAGE_MIN = 0.5

# Findings rules (§11)
REVIEW_CAPACITY_PICKUP_RATIO = 1.5
REVIEW_CAPACITY_MIN_WAIT_SHARE = 0.15
REVIEW_CAPACITY_HIGH_WAIT_SHARE = 0.30
QUEUE_GROWTH_WEEK_SHARE = 0.5
QUEUE_GROWTH_MIN_UNSERVED_SHARE = 0.20
QUEUE_GROWTH_HIGH_UNSERVED_SHARE = 0.50
CONCENTRATION_SHARE = 0.60
CONCENTRATION_HIGH_SHARE = 0.75
MERGE_BLOCKED_SHARE = 0.15
MERGE_BLOCKED_HIGH_SHARE = 0.30
MERGE_BLOCKED_P50_HOURS = 24.0
CI_WAIT_SHARE = 0.15
CI_WAIT_HIGH_SHARE = 0.30
REWORK_ROUNDS = 2.5
REWORK_POST_REVIEW_SHARE = 0.5
WASTE_SHARE = 0.15
WASTE_HIGH_SHARE = 0.25
LOST_WHILE_WAITING_MIN = 5
GUARDRAIL_REVERT_RATE_DELTA = 0.01   # Absolute revert-rate rise: 0.01 = 1 percentage point
EXTERNAL_PICKUP_RATIO = 2.0
```

`insights/analytics/__init__.py` defines `ANALYTICS_VERSION = "1.4.0"` and `derive_key(location_dimension: str, directory_depth: int) -> str`, returning `f"{ANALYTICS_VERSION}|{THRESHOLDS_VERSION}|{location_dimension}|depth={directory_depth}"`. Worker fact/repository identity writes and API readiness (`06` §5.1) call this shared function rather than construct separate strings.

## 2. State machine (`insights/analytics/timeline.py`)

### 2.1 Input and output

```python
@dataclass(frozen=True, slots=True)
class Interval:
    state: str                    # coding | waiting_reviewer | waiting_author | waiting_ci | waiting_merge | closed
    start_at: datetime
    end_at: datetime | None       # None when ongoing

@dataclass(frozen=True, slots=True)
class TimelineResult:
    ready_at: datetime | None
    intervals: tuple[Interval, ...]
    approved_at: datetime | None  # First entry into waiting_merge
    review_rounds: int            # Entries into waiting_author due to reviewer feedback (§2.4)
    state_at_end: str | None      # Last waiting state before unmerged closure

def build_timeline(pr: PrInput, events: Sequence[Event], ci_intervals: Sequence[tuple[datetime, datetime]], now: datetime) -> TimelineResult
```

PrInput includes author_login, created_at, current is_draft, merged_at, closed_at, state. ci_intervals is always empty in P0 (added M8).

### 2.2 Definitions

- **Author identity**: author_key=author_login.lower(); null login (deleted account) gives None, meaning unknown. All author comparisons use author_key; None always means "not the author", without calling .lower().
- **Human account**: nonempty login, not a bot, and author_key is None or login.lower()!=author_key.
- **Reviewer feedback**: human review with state in {CHANGES_REQUESTED, COMMENTED}.
- **Approval**: human review with state APPROVED.
- **Author update**: any commit or force_push; author's own comment/review (any state, representing a reply) or review_requested. With unknown author, only commits/force pushes count.
- **ready_at**:
  - first_ready = first ready_for_review time; first_convert = first convert_to_draft time.
  - If first_ready exists and first_convert is absent or later: created as draft; ready_at=first_ready.
  - Otherwise, if currently draft with no convert_to_draft: created draft and never ready; ready_at=None.
  - Otherwise ready_at=created_at.
- **end_at**: merged_at if merged, final closed_at if closed unmerged, None if open. horizon=end_at or now.
- **Closed periods**: only **paired** closure/reopen events count: a closed event followed by reopened before horizon defines closed → first subsequent reopened. Ignore unpaired final/merge-associated closure, even one or two seconds before merged_at. Closed intervals are not waiting and do not contribute to ledger, waiting_share, attribution, or detail ledger_hours; they identify whether a PR was open at a time. Milestone durations (cycle_hours, pickup_hours, etc.) are timestamp differences and include closure by definition. Document this uncommon-case trade-off in DECISIONS.
- **first_commit_at**: minimum commit payload.authored_at, or None if no commits.

### 2.3 State variables and decision

Apply events chronologically and maintain:

- latest_review: dict[reviewer_lower, (state, review_id)] stores each human reviewer's latest **decisive** APPROVED/CHANGES_REQUESTED review. COMMENTED leaves it unchanged, matching GitHub semantics. Original submitted payload.state is restored during normalization (`04` §5.3), so approvals hold until dismissal. A review_dismissed removes the current decision only when review IDs match; if no ID, use payload.review_author. Dismissing an older review does not undo a later decision.
- last_feedback_at: most recent CHANGES_REQUESTED/COMMENTED reviewer feedback.
- last_author_update_at: most recent author update.
- draft: convert_to_draft sets true; ready_for_review sets false.
- paused: **paired** closed (§2.2) sets true; reopened sets false. Unpaired closure does not alter state.

Derived values:

```text
outstanding_changes = any(state == "CHANGES_REQUESTED" for state, _ in latest_review.values())
has_approval        = any(state == "APPROVED" for state, _ in latest_review.values())
approved            = has_approval and not outstanding_changes
awaiting_author     = last_feedback_at is not None and (last_author_update_at is None or last_feedback_at > last_author_update_at)
ci_running(t)       = any CI interval [s, e) with s <= t < e
```

Decision priority, top to bottom:

```text
if paused:            closed
elif draft:           waiting_author
elif approved:        waiting_merge
elif awaiting_author: waiting_author
elif ci_running(t):   waiting_ci
else:                 waiting_reviewer
```

### 2.4 Algorithm

```text
ready_at = compute_ready_at(...)
coding_start = min(first_commit_at, ready_at or horizon) if first_commit_at else created_at
if ready_at is None:
    return one coding interval [coding_start, end_at] (end_at may be None)
if coding_start < ready_at: append coding interval [coding_start, ready_at]
Initialize state from all events with occurred_at <= ready_at (including draft reviews), then set draft=False
boundaries = sorted unique event times in (ready_at, horizon) and CI interval endpoints in that range
current = evaluate(ready_at); seg_start = ready_at
for t in boundaries:
    Apply events with occurred_at == t in fixed kind order: closed, reopened, merged, convert_to_draft,
      ready_for_review, review_dismissed, review, review_requested, review_request_removed, commit, force_push,
      comment, labeled, unlabeled, cross_referenced)
    new = evaluate(t)
    if new == waiting_author and current != waiting_author and not draft and this batch contains reviewer feedback:
        review_rounds += 1        # Reopening or leaving draft does not add a round
    if new != current:
        emit interval [seg_start, t]
        current = new; seg_start = t
Emit interval [seg_start, end_at] (None when PR is open)
Merge adjacent same-state intervals; discard zero-length intervals
approved_at = first waiting_merge interval start_at
state_at_end = final interval state if closed unmerged
```

Events exactly at horizon (merge/final closure) are outside the loop and create no closed interval. Closed intervals represent temporary closure before reopening and can never be last.

### 2.5 Boundary cases (unit test each)

1. Created non-draft, one approval then merge: waiting_reviewer → waiting_merge.
2. Changes requested → author push → approval: waiting_reviewer → waiting_author → waiting_reviewer → waiting_merge; review_rounds=1.
3. Author replies with comment only, no push: waiting_author → waiting_reviewer.
4. Author requests review again: same as case 3.
5. Created draft → ready: coding before ready.
6. Returned to draft after ready: waiting_author during draft.
7. Approval dismissed: waiting_merge before dismissal; waiting_reviewer (or waiting_author) afterward.
8. Two reviewers: A requests changes, B approves; not approved. After author push A still has CHANGES_REQUESTED; state waiting_reviewer.
9. Merged without approval: no waiting_merge, approved_at=None.
10. Closed unmerged, no review: waiting_reviewer only, state_at_end=waiting_reviewer.
11. Closed → reopened → merged: temporary closed interval excluded from waiting/ledger; observation inside it does not count PR as open.
12. Bot/self reviews count as neither reviewer feedback nor approval.
13. Created draft, closed without ever ready: one coding interval.
14. First commit after ready (PR opened before commit): no coding interval.
15. Open PR: final interval end_at=None.
16. CI (M8) splits waiting_reviewer into waiting_ci; waiting_author and waiting_merge take priority over CI.
17. Reviewer approves, then submits COMMENTED: still approved/waiting_merge, no additional review round (approved overrides awaiting_author).
18. Upstream dismissal: review at t1 is currently DISMISSED; dismissal previousReviewState=APPROVED at t2. State waiting_merge on t1–t2, waiting_reviewer afterward; repeated synchronization produces identical intervals.
19. Same reviewer approves r1, then r2; r1 dismissed: still approved.
20. Deleted author/null login: valid human reviews still count without error; author comments cannot be identified, so only commit/force_push updates count.
21. Merged PR has closed one second before merged_at with no reopened: no closed interval, last state waiting_merge (or actual premerge waiting state); invariants hold.
22. Two reviewers' approvals dismissed after push in same second by same actor, different review IDs: retain/apply both dismissals (`04` §5.4); return to waiting_reviewer.

### 2.6 Invariants (pure `timeline.check_invariants(result, pr) -> list[str]`; CLI in 01 M4; asserted in tests)

1. Intervals sorted by start_at, non-overlapping and contiguous; temporary closure uses closed intervals, not gaps.
2. Ended PR: all post-ready intervals including closed cover [ready_at, end_at); non-closed durations sum to (end_at-ready_at) minus closed durations, to the second.
3. coding intervals occur only before ready_at.
4. No start_at>=end_at; only final interval may have end_at=None, only for open PRs; final interval is never closed.

### 2.7 Using intervals at as_of

The state machine is causal (state at t depends only on earlier events), so intervals derived once answer historical queries:

- Clip intervals to [start_at, min(end_at or +∞, as_of)); discard start_at>=as_of.
- Point-in-time state: interval with start_at<=as_of<end_at, or null end_at.
- **Open at t**: ready_at<=t, end_at null or >t, and containing interval is waiting, not closed. Share this predicate across §9.2 queue, §9.6 risks, §17 open population, and meta.sample.open_prs_at_as_of.

## 3. PR facts (`insights/analytics/facts.py`)

`compute_facts(pr, events, timeline, *, default_branch, location_rules, now) -> PrFacts`; fields correspond to `03` §2.5:

| Field | Computation |
|---|---|
| `first_commit_at` | §2.2 |
| `ready_at` | `timeline.ready_at` |
| human_reviews | Human APPROVED/CHANGES_REQUESTED/COMMENTED events, including later-dismissed reviews because they occurred |
| first_review_at | Earliest human review, including before ready |
| first_response_at | min(first_review_at, earliest human comment) |
| first_approval_at | Earliest approval |
| `approved_at` | `timeline.approved_at` |
| end_at, merged_at, closed_at | closed_at set only for closed unmerged PRs |
| coding_hours | max(0, ready_at-first_commit_at) if both exist; otherwise None |
| pickup_hours | max(0, first_review_at-ready_at) if both exist; otherwise None |
| review_hours | approved_at-first_review_at if both exist; otherwise None |
| merge_hours | merged_at-approved_at if both exist; otherwise None |
| cycle_hours | merged_at-(first_commit_at or created_at) if merged; otherwise None |
| `review_rounds` | `timeline.review_rounds` |
| feedback_before_approval | Reviewer feedback before first_approval_at, or all feedback if never approved |
| commits_after_first_review | Commits after first_review_at, zero if no review |
| force_pushes_after_first_review | Same for force pushes |
| updates_after_approval | Commits + force pushes after approved_at and before merge/closure; zero without approved_at |
| distinct_approvers | Number of distinct approving reviewers |
| second_approval_wait_hours | First approval by second distinct reviewer minus first_approval_at; None with fewer than two |
| merged_without_approval | Merged and approved_at is None |
| review_requested_before_first_review | review_requested before first_review_at, or any time if no review |
| size_lines, size_bucket | additions+deletions, classified by SIZE_BUCKETS |
| ready_weekday, ready_hour | From ready_at in UTC |
| `state_at_close` | `timeline.state_at_end` |
| `ci_covered` | `len(ci_intervals) > 0` |
| `is_bot_author`, `is_backport`, `external_contributor`, `locations`, `location_source` | §4 |
| Revert / reland / supersession / close_class / late_rejection / author_open_prs_at_ready ("linkage fields") | Repository linking only (§4.3–§4.7). compute_facts sets defaults for new rows; derive_prs **does not overwrite** existing linkage fields in ON CONFLICT DO UPDATE, avoiding cleared intermediate state between linking runs |

Compute durations with timedelta.total_seconds()/3600 without rounding until output.

## 4. Classification and linking (`insights/analytics/classify.py`)

### 4.1 Population

- is_bot_author: from normalization.
- is_backport: base_ref differs from default branch; false if default unknown.
- **Flow PR**: not is_bot_author and not is_backport and ready_at is not None. Never-ready drafts have not entered review. All metrics use flow PRs except meta.excluded counts (bot_prs, backport_prs, never_ready_drafts).
- `external_contributor`:`author_association ∈ {CONTRIBUTOR, FIRST_TIME_CONTRIBUTOR, FIRST_TIMER, NONE}`.

### 4.2 Location

Compute locations/location_source from LOCATION_DIMENSION and fallback chain:

1. label:<prefix>: deduplicated, sorted labels matching prefix case-insensitively, preserving text; e.g. ["area-System.Net.Http"]. If none, fall back.
2. codeowners (P1, rules available): **last matching** CODEOWNERS rule per file (pathspec gitwildmatch); location="codeowners:"+pattern; deduplicate/sort; fall back if none.
3. directory: first DIRECTORY_DEPTH path segments per file, "dir:"+prefix (root files "dir:/"); take top DIRECTORY_LOCATIONS_PER_PR by descending file count, then ascending name.
4. No match: ["unclassified"].

directory mode starts at step 3; codeowners mode at step 2.

### 4.3 Closed classification (closed unmerged flow PRs)

First match wins:

1. **superseded**: cross_referenced on this PR (GitHub records references on the **referenced** PR; source is the mentioning PR) from same-repository merged PR with same known author_key and merge in [created_at, closed_at+SUPERSEDE_WINDOW_DAYS]; or another known same-author/head_ref PR created within SUPERSEDE_WINDOW_DAYS after closure and merged. Use current DB source state/author/merge if present; event source_state is a possibly stale fetch snapshot. Use payload only if source absent. Choose earliest merged candidate for superseded_by_pr_id.
2. **no_review**:`human_reviews == 0`.
3. **rejected**: final closed event by a human non-author.
4. **abandoned**: otherwise (author or bot closure).

late_rejection = close_class == "rejected" and (closed_at-ready_at >= LATE_REJECTION_DAYS days or review_rounds >= LATE_REJECTION_ROUNDS).

Derived during analysis: lost_while_waiting = close_class in {no_review, abandoned} and state_at_close == waiting_reviewer.

### 4.4 Revert detection (repository link_repo)

A **revert PR** satisfies any of:

- Title matches ^Revert\s+"(?P<title>.+)"\s*$;
- body_excerpt matches (?m)^Reverts\s+(?P<repo>[\w.-]+/[\w.-]+)#(?P<num>\d+)\b;
- Title starts with Revert case-insensitively and a commit has nonempty payload.reverts.

Resolve original PR in order, stopping at first match:

1. Body Reverts owner/repo#N, same repository case-insensitively → PR N;
2. Reverts SHA prefix matches repository merge_commit_oid or commit OID, merged before revert creation;
3. Captured title matches most recent identically titled default-branch PR merged before revert creation.

When original found and revert merged: original.reverted_by_pr_id=revert.id, reverted_at=revert.merged_at; revert.is_revert=True, reverts_pr_id=original.id. Unmerged revert sets is_revert only.

If original is itself a revert (Revert of Revert of X), this PR is a **reland**: is_reland=True, reland_of_pr_id=original.reverts_pr_id; do not process as revert.

### 4.5 Reland detection

Title matching ^(Reland|Re-land|Reapply|Re-apply)\b case-insensitively sets is_reland=True. Resolve reland_of_pr_id in order: first #N in title/body referring to reverted PR; quoted title equal to reverted PR title; text after keyword stripped of quotes equal to reverted PR title.

**Deferred (DECISIONS and README Not done)**: the design calls for true delivery time from the chain's first PR to final merge for supersession/revert/reland. This version stores links and per-PR cycle time only; links support later chain timing.

### 4.6 Concurrent author PRs (P1, M9)

author_open_prs_at_ready counts other same-author flow PRs with ready_at_other<=t<(end_at_other or +∞) at each flow PR's ready time. Group/sort/scan by author, O(n log n), not O(n²) pairwise comparisons. Unknown authors are excluded and receive None.

### 4.7 Implementation and timing

- Pure classify.link_prs(prs: Sequence[LinkInput], *, repo_full_name: str, default_branch: str) -> dict[int, LinkResult]: inputs PR number/author/title/body/head/base/timestamps/merge OID, commit OIDs/reverts, cross references, computed facts; outputs all §4.3–§4.6 linkage fields: close_class, late_rejection, revert/reland/supersession, author_open_prs_at_ready. Eval calls it directly (`08` §2.5).
- I/O link_repo(session, repo_id) in sync/derive.py runs at each sync/rederive completion over **all repository PRs** (bounded by backfill retention, thousands for dotnet/runtime). Load required number/author/title/body/head/times/merge OID/commit OIDs/reverts/cross references/facts once; call link_prs; bulk-update changed linkage rows only, incrementing data_version in the same transaction (`05` §12.3). Full processing avoids missing old links when only changed PRs are inspected.

## 5. Statistics utilities (`insights/analytics/stats.py`)

- percentile(values, q, min_samples) -> float | None: None below sample gate; otherwise numpy.percentile(values, q, method="linear").
- bootstrap_diff(current, previous, statistic, seed) -> tuple[float, float]: seeded numpy.random.default_rng, BOOTSTRAP_ITERATIONS independent resamples with replacement, vectorized statistic differences, BOOTSTRAP_CI interval. Support median, selected percentile, mean, and ratio (paired PR numerator/denominator resampling).
- `seed_for(params_hash, metric_name) -> int`:`int.from_bytes(sha256(f"{params_hash}:{metric_name}").digest()[:8], "big")`.
- kaplan_meier(...): P1, §15.

Wilson intervals mentioned in the design are for offline rate-threshold calibration, not runtime; omit implementation.

## 6. Data loading and periods (`insights/analytics/dataset.py`)

### 6.1 Periods

- Current: from_dt=from 00:00Z, to_excl=(to+1 day) 00:00Z, length L days.
- Previous: [from_dt-L days, from_dt).
- as_of=min(to_excl, minimum relevant last_synced_at), even for historical to. Stalled sync limits observation to collected coverage. last_synced_at is catch-up incremental start at latest checkpoint, meaning consistent through that time (`04` §6.3). Readiness guarantees non-null and as_of>from_dt (`06` §5.1). period.complete=(as_of==to_excl). Previous observation time is from_dt.
- end_dt=min(to_excl, as_of)=as_of. All current-period predicates from §7 (merge, close, ready, review, clipped weeks/cohort) use [from_dt, end_dt). For to=today, omit post-as_of writes such as newly fetched merges, avoiding counting a PR as both merged and open at as_of. Previous window [from_dt-L days, from_dt), observed at from_dt.
- comparison_available iff every covered_since<=previous start; otherwise all previous-related fields None.

### 6.2 Loaded data

Execute queries in **one REPEATABLE READ, read-only transaction** so concurrent worker writes cannot mix states. Return immutable Dataset to pure computation in asyncio.to_thread:

1. Flow pr_facts plus PR number/title/url/author_login/is_draft/created_at: ready_at<to_excl and (end_at null or >=previous start).
2. Those PRs' intervals.
3. At-risk baselines: completed waiting intervals ending in [from_dt-AT_RISK_FALLBACK_DAYS, as_of), with repo/state/end_at/duration. Start relative to from_dt, not as_of, to cover previous-period risk observation and its preceding 180 days.
4. Human review events in both periods: reviewer_lower, occurred_at, pr_id, for flow PRs.
5. Current merged/closed bot/backport counts, and closed never-ready drafts (meta.excluded, `06` §4.3). One category per PR, priority bot → backport → never_ready_draft.
6. Repository metadata: default branch, data_version, covered_since, last_synced_at.
7. P1: mapped CI runs and location owner counts.
8. P1: historical merged flow PR (merged_at, cycle_hours) in [from_dt-L days-90 days, from_dt) for predictability; independent of item-1 filter (§16).

## 7. Efficiency metrics (`insights/analytics/efficiency.py`)

Notation: M=current merged flow PRs with merged_at∈[from_dt,end_dt); M_prev uses [from_dt-L days,from_dt); Cl=finally closed unmerged flow PRs whose final closed_at falls in current window. Use final outcome: PRs closed then reopened do not count (later delivered or ongoing), at most once per PR, so waste measures undelivered work. All metrics use Metric (`06` §4.2), with identical computation for current/previous.

| Metric | Definition | Minimum sample | Significance |
|---|---|---|---|
| merged_prs | count(M) | None | Not computed (null) |
| effective_throughput | count(M) - count(p∈M with p.reverted_at<as_of) - count(p∈M with p.is_revert) | None | Not computed (null) |
| cycle_time_p50_hours / p90 | Quantile of M.cycle_hours | 20 / 30 | Bootstrap median / p90 difference |
| stage_p50_hours.{coding,pickup,review,merge} | Median non-null corresponding values in M | 20 | Bootstrap |
| merged_within_n_days | C=flow PRs ready in [from_dt,min(to_excl,as_of)-N days], each with full N-day observation; share merged_at-ready_at<=N days; extra.n_days=N | Rate gate | Bootstrap mean |
| waiting_share | Waiting over **whole cycle** (flow-efficiency proxy): Σ_M(waiting_reviewer+waiting_ci+waiting_merge) / Σ_M(coding_hours+all four waiting-state durations). Null coding counts as zero; exclude closed from both. Ledger (§8) uses post-ready time only, a different denominator | 20 | Bootstrap ratio, paired per PR |
| waste_share | D=M∪Cl; (non-superseded Cl count + reverted M count)/count(D) | Rate gate | Bootstrap mean |
| avg_review_rounds | Mean M.review_rounds | 20 | Bootstrap mean |
| post_review_commit_share | Share of M with commits_after_first_review>0 | Rate gate | Bootstrap mean |
| review_concentration_top_k | Top K reviewers' share of period human review events; extra.k=K | ≥30 reviews | Not computed |
| revert_rate | count(p∈M with reverted_at<as_of) / count(M) | Rate gate | Bootstrap mean |
| pr_size_p50_lines | Median M.size_lines | 20 | Bootstrap |
| `predictability` | P1,§16 | | |

**Rate gate**: denominator>=MIN_RATE_DENOMINATOR and events>=MIN_RATE_EVENTS; else value=None, status="insufficient_sample", extra={"events":k,"denominator":n}.

**Significance** (§10): bootstrap difference 90% interval excludes zero and |change_rel|>=CHANGE_MIN_RELATIVE.

## 8. Time ledger

Population M; per PR use waiting intervals in [ready_at,merged_at], excluding coding/closed:

- `merged_prs = |M|`,`previous_merged_prs = |M_prev|`.
- State pr_hours=sum durations; total_pr_hours=sum four states; share=pr_hours/total_pr_hours, zero when total zero.
- Compute previous_pr_hours/previous_total_pr_hours/previous_share from M_prev; change_pp=(share-previous_share)*100. Null previous fields when unavailable.
- ci_coverage=share of M with ci_covered; ci_data_available=CI_SOURCE!="none" and ci_coverage>0 (always false P0).
- Output structure: `06` §4.5.

## 9. Bottleneck analysis (`insights/analytics/bottlenecks.py`)

### 9.1 Location statistics

For each location ℓ: M_ℓ={p∈M:ℓ∈p.locations}; weight w_p=1/len(p.locations) for additive totals.

| Field | Definition |
|---|---|
| `merged_prs` | count(M_ℓ) |
| pickup_p50_hours | M_ℓ pickup median (≥MIN_SAMPLES_LOCATION_P50) |
| pickup_ratio_vs_rest | pickup_p50(M_ℓ)/pickup_p50(M-M_ℓ), None if either missing |
| waiting_reviewer_pr_hours | Σ w_p × PR waiting_reviewer duration |
| previous_waiting_reviewer_pr_hours | Same over M_prev at ℓ; null if previous unavailable |
| waiting_reviewer_share | waiting_reviewer_pr_hours / ledger waiting_reviewer.pr_hours; zero if denominator zero |
| inflow / outflow | Location flow PR counts with ready_at / effective first-review fr (§9.2) in current window |
| at_risk_prs | At-risk PR count at ℓ (§9.6) |
| owners_count | P1: area owner count for labels, rule owner count for codeowners; otherwise None |

Merge locations below MIN_LOCATION_PRS into "other": recalculate merged_prs/inflow/outflow/at_risk_prs on **deduplicated union**; sum weighted reviewer hours (current/previous); recompute medians/ratios on union; owners_count=null. Other locations sort by descending reviewer hours then name; keep MAX_LOCATIONS_IN_SNAPSHOT, fold remaining into other; other always last. Multi-repo names are "{repo}:{location}".

### 9.2 Review queue (weekly)

Use this period's flow cohort: opened or with human activity this period, shared across weekly points. Weeks start Monday, clipped to [from_dt,end_dt). Effective first review fr=max(first_review_at,ready_at); draft-reviewed PRs leave at ready; null if never reviewed. For each [ws,we), including unmerged flow PRs:

- inflow=count ready_at∈[ws,we).
- outflow=count fr∈[ws,we).
- open_at_week_end=count open at we (§2.7; not temporarily closed) and (fr null or fr>=we).

Output week_start (clipped start date) and days (days inside period).

- weeks_total=number of weeks; weeks_inflow_exceeds_outflow=count weeks with inflow>outflow.
- open_growth_rel=last-week open_at_week_end / first-week value - 1, null when first zero.
- net_inflow_share=(Σinflow-Σoutflow)/Σinflow, null for zero inflow. Outflow can serve prior-period demand, giving negative values. This is a net period flow gap, not the unreviewed fraction of a particular new-PR cohort.
- open_at_week_end/open_growth_rel are display-only; this cohort omits inactive older PRs, so they cannot establish full-backlog growth. Findings use arrival/first-review net gap.

### 9.3 Merge blocking

Use M with approved_at (merge median already in efficiency.stage_p50_hours.merge):

- approved_merged_prs: count.
- second_approval_share: share with distinct_approvers>=2; second_approval_wait_p50_hours: median second_approval_wait_hours with ≥10 samples.
- post_approval_update_share: share with updates_after_approval>0; post-approval commits/force pushes approximate rebases/conflicts.
- ci_after_approval_p50_hours: P1 median overlap between each PR's CI intervals and [approved_at,merged_at]. waiting_merge overrides CI in state intervals, so calculate directly from CI intervals; None P0.

Ratios are None below denominator 10.

### 9.4 Impact ranking (Pareto)

Split waiting_reviewer by top-five §9.1 locations, folding rest into location other; add one each for waiting_author/ci/merge. Fields cause/location/pr_hours/share of total ledger; sort descending pr_hours.

### 9.5 What-if

For stage s∈{pickup,merge} (CI from M8), target T=WHAT_IF_TARGET_HOURS[s]:

```text
For each p∈M with non-null cycle_hours:
    x = stage duration (pickup_hours, merge_hours, or total waiting_ci); missing is zero
    new_cycle = cycle_hours - max(0, x - T)
affected_prs = count x>T
cycle_p50_before / cycle_p50_after = medians of the two cycle distributions, minimum 20
change_rel = after/before-1; None if either median missing or before zero
Omit what-if when change_rel is None (no list entry; finding.what_if=None)
```

Location what-if for review_capacity: cap pickup only for PRs containing ℓ; other PRs unchanged; report median change across all M.

### 9.6 At-risk PRs

1. Candidates: flow PRs open at as_of (§2.7), excluding temporary closed intervals. When as_of is current (to=today), exclude currently draft PRs.
2. Find containing interval, state s, age_hours=as_of-interval.start_at.
3. Group baselines by (repo,state); each PR uses **its own repository**, including multi-repo queries. Completed same-state interval durations ending in [as_of-90 days,as_of); with ≥30 use p85/p95, baseline_source="90d". Otherwise try 180 days ("180d"); if still insufficient use AT_RISK_DEFAULT_HOURS[s] as p85, double as p95 ("default").
4. Risk if age_hours>p85; critical above p95, otherwise warning.
5. Sort descending age_hours/threshold_hours, ascending (repo,number). Snapshot retains AT_RISK_MAX_ITEMS (`06` §4.8; threshold_hours=p85, critical_threshold_hours=p95). at_risk_summary={total,critical,by_state}, always all four states with zeros. Full list via /v1/insights/delivery/prs?at_risk=true.

### 9.7 Trends

Populate snapshot trend (`06` §4.10):

- State-share changes already in time_ledger.states[*].change_pp; do not duplicate.
- bottleneck_shift: if comparison available, largest change_pp≥5; format `f"{state} share {change_pp:+.1f}pp vs previous period"`, e.g. waiting_ci share +7.2pp vs previous period; else None.
- `attribution`:§9.12.

### 9.8 Review load

reviewers=human reviewer count in period; reviews=human review count; distribution=top 10 {reviewer,reviews,share}, sorted descending reviews then login. Concentration is efficiency.review_concentration_top_k. **One of only two individual-level snapshot fields (other is risk author); neither enters evidence pack.**

### 9.9 Waste, rework, guardrail

- waste: closed_unmerged=count(Cl), four by_class counts, lost_while_waiting, late_rejections; wasted_review_share=period human reviews on non-superseded Cl / all period human reviews, None if denominator<30; wasted_pr_hours=ledger totals of those PRs plus reverted M PRs.
- rework: reverts=M reverted before as_of; revert_prs=M with is_revert; relanded=reverted originals with merged reland. Latest ten revert_chains descending revert merge: {original,revert,reland,exposure_hours,revert_pr_cycle_hours}; exposure_hours=revert.merged_at-original.merged_at, revert_pr_cycle_hours=revert.merged_at-revert.created_at; PR references {number,url}.
- guardrail: cycle_time_p50_change_rel=efficiency.cycle_time_p50_hours.change_rel, revert_rate/previous_revert_rate per §7, revert_rate_change_pp=difference×100, verdict:
  - tradeoff_suspected: significant cycle p50 decline with change_rel<=-0.10 and revert_rate-previous_revert_rate>=GUARDRAIL_REVERT_RATE_DELTA; both rate samples pass gates.
  - watch: revert-rate increase condition only.
  - Otherwise ok, including either revert rate None.

### 9.10 Hypothesis signals (`signals`)

| Field | Definition |
|---|---|
| large_pr_share | Share of M with size_lines>=LARGE_PR_LINES |
| merged_without_approval_share | Share of M with merged_without_approval |
| fast_large_approval_share | Share of M with size_lines>=FAST_APPROVAL_MIN_LINES, first_approval_at-ready_at<=FAST_APPROVAL_MINUTES minutes, feedback_before_approval==0 |
| external_pickup_ratio | External/internal pickup medians; ≥10 samples in each group |
| at_risk_reviewer_top_location_share | Among waiting_reviewer risks, maximum location count / PR count; multilocation PR counts once per location; None with fewer than four PRs |

All are Metric with previous comparison; PR shares use M_prev, risk signals use risks observed at from_dt.

### 9.11 Weekly series (`series`)

Current/previous series use §9.2 weeks. Attribute merged flow PRs by merged_at: week_start, days, merged count; cycle_p50_hours, pickup_p50_hours, pr_size_p50_lines (each needs MIN_SAMPLES_WEEKLY_P50, else None); waiting_reviewer_share/waiting_ci_share of that week's merged ledger (None with zero merges); reverts (merged that week, reverted before as_of). Narrative uses effect/persistence (`07` §4.1). Previous=[] if unavailable.

### 9.12 Change attribution (`trend.attribution`)

Medians are not additive; use **mean hours per merged PR** (design: means or cumulative PR-hours):

- Components c∈{coding,waiting_reviewer,waiting_author,waiting_ci,waiting_merge}: current_c=sum component hours in M / count(M), coding_hours null treated as zero and waiting from ledger pr_hours; previous_c similarly for M_prev; change_c=current_c-previous_c.
- cycle_mean_hours={current: mean M.cycle_hours, previous: mean M_prev.cycle_hours, change}.
- `total_increase_hours = Σ_c max(0, change_c)`;`total_decrease_hours = Σ_c max(0, -change_c)`.
- share_of_increase_c=max(0,change_c)/total_increase_hours; share_of_decrease_c=max(0,-change_c)/total_decrease_hours; zero with zero denominator.
- locations: same order/count as bottleneck_analysis.locations; change=waiting_reviewer_pr_hours/count(M)-previous_waiting_reviewer_pr_hours/count(M_prev); gross=Σ_ℓ max(0,change_ℓ), sum of positive location changes:
  - share_of_reviewer_increase=max(0,change)/gross, zero if gross zero.
  - share_of_increase=states.waiting_reviewer.share_of_increase×share_of_reviewer_increase. Allocate **net** positive reviewer growth in proportion to positive location changes. Location sum equals reviewer state's share, never >1; mere redistribution with zero net reviewer growth yields all zeros.
- large_prs: current=sum cycle_hours for M with size_lines>=LARGE_PR_LINES / count(M), previous analogously; change; share_of_increase=min(1,max(0,change)/cycle_mean_hours.change), zero when total cycle change<=0.
- attribution=None if comparison unavailable or either merged count<MIN_SAMPLES_P50.
- Hours: 2 decimals; shares: 4; structure `06` §4.10.

## 10. Comparison and significance

- Compute previous period with identical functions.
- change_abs=value-previous; change_rel=change_abs/previous, None if previous missing/zero.
- Significance only for §7 bootstrap metrics: 90% interval excludes zero, |change_rel|>=0.10; seed_for(params_hash,metric_name).

## 11. Findings and headline (`insights/analytics/findings.py`)

Rules populate snapshot bottlenecks (`06` §4.6). Each yields zero/one finding; review_capacity allows one per location, excluding other. Evidence lists {label,value,unit,ref}, with JSON Pointer ref and matching value (Metric uses .value). Null condition values do not satisfy rules. With count(M)<MIN_SAMPLES_P50 return []; small samples show concrete risks only, not statistical "main bottleneck" conclusions.

| Type (id) | Condition | Severity | impact_pr_hours | Recommendation (English template) |
|---|---|---|---|---|
| review_capacity:{location} | pickup_ratio_vs_rest>=1.5, waiting_reviewer_share>=0.15, merged_prs>=10 | high if share≥0.30, else medium | Location waiting_reviewer_pr_hours | "Add reviewers or code owners for {location}, enable team auto-assignment, and set a one-business-day first-review SLA." |
| review_queue_growth | weeks_inflow_exceeds_outflow / weeks_total>=0.5 and net_inflow_share>=0.20 | high if net_inflow_share>=0.50 | Ledger waiting_reviewer.pr_hours | "New PRs arrive faster than they get a first review: rebalance review load or limit work in progress until first reviews keep up." |
| review_concentration | review_concentration_top_k>=0.60 | high at ≥0.75 | 0 | "Spread reviews through a rotation or CODEOWNERS so a few reviewers are not a single point of failure." |
| merge_blocked | waiting_merge.share>=0.15 or stage_p50_hours.merge>=24 | high if share≥0.30 | waiting_merge.pr_hours | If second_approval_share>=0.5: "Review whether two approvals are needed for low-risk changes." Else: "Reduce post-approval rebase friction, for example with a merge queue." |
| ci_wait (P1) | ci_data_available and waiting_ci.share>=0.15 | high at ≥0.30 | waiting_ci.pr_hours | If queue_p50_minutes>=run_p50_minutes: "Add CI capacity or reduce queued jobs." Else: "Speed up the slowest workflows." Append " Fix flaky tests that pass only on rerun." if flaky_rerun_rate>=0.10 |
| rework_high | avg_review_rounds>=2.5 or post_review_commit_share>=0.5 | medium | waiting_author.pr_hours | "Agree on the approach before coding (issue or design note) and keep PRs small to cut review rounds." |
| waste_high | waste_share>=0.15 or lost_while_waiting>=5 | high if waste_share>=0.25 | wasted_pr_hours | "Review late rejections and PRs lost while waiting for review; align on scope earlier and triage stale PRs." |
| quality_guardrail | guardrail.verdict!="ok" | high for tradeoff_suspected, else medium | 0 | "Faster delivery may be costing quality: check whether review depth dropped before pushing speed further." |
| external_contributor_wait | external_pickup_ratio>=2.0 | medium | External PR waiting_reviewer duration sum | "Set up a triage rotation so community PRs get a first review sooner." |

Titles and evidence; {i} is location index in bottleneck_analysis.locations:

| Type | title | evidence (label → ref) |
|---|---|---|
| `review_capacity` | `First-review wait concentrated in {location}` | `First-review wait vs rest of repo` → `/bottleneck_analysis/locations/{i}/pickup_ratio_vs_rest`;`Share of reviewer-waiting time` → `/bottleneck_analysis/locations/{i}/waiting_reviewer_share`;`Median first-review wait` → `/bottleneck_analysis/locations/{i}/pickup_p50_hours` |
| `review_queue_growth` | `Review demand exceeds first reviews` | `Weeks with inflow above outflow` → `/bottleneck_analysis/review_queue/weeks_inflow_exceeds_outflow`;`Share of new review demand not yet served` → `/bottleneck_analysis/review_queue/net_inflow_share` |
| `review_concentration` | `Reviews concentrated on a few people` | `Share of reviews by top K reviewers` → `/efficiency/review_concentration_top_k` |
| `merge_blocked` | `Approved PRs wait long to merge` | `Share of PR time waiting to merge` → `/time_ledger/states/waiting_merge/share`;`Median approval-to-merge time` → `/efficiency/stage_p50_hours/merge`;`Share with a second approval` → `/bottleneck_analysis/merge_blockers/second_approval_share` |
| `ci_wait` | `CI waiting is a large share of PR time` | `Share of PR time waiting on CI` → `/time_ledger/states/waiting_ci/share`;`Median CI queue time` → `/bottleneck_analysis/ci/queue_p50_minutes`;`Median CI run time` → `/bottleneck_analysis/ci/run_p50_minutes` |
| `rework_high` | `High rework after review` | `Average review rounds` → `/efficiency/avg_review_rounds`;`Share with commits after first review` → `/efficiency/post_review_commit_share` |
| `waste_high` | `Significant work never ships` | `Waste share` → `/efficiency/waste_share`;`PRs lost while waiting for review` → `/waste/lost_while_waiting` |
| `quality_guardrail` | `Speed may be costing quality` | `Median cycle time change` → `/guardrail/cycle_time_p50_change_rel`;`Revert rate` → `/guardrail/revert_rate` |
| `external_contributor_wait` | `External contributors wait longer for a first review` | `First-review wait, external vs internal` → `/signals/external_pickup_ratio` |

- id={type} or {type}:{location}; only review_capacity includes location.
- impact_share=impact_pr_hours/time_ledger.total_pr_hours; zero if total zero.
- Sort by descending impact_pr_hours → severity high>medium>low → ascending id; rank starts at one. Cumulative waiting sets priorities.
- what_if: location pickup for review_capacity, merge for merge_blocked, CI for ci_wait, otherwise None.

**Headline** (English deterministic template):

1. Efficiency, first applicable:
   - Significant cycle p50 change: `"Median cycle time rose|fell {|change_rel|×100:.0f}% ({previous:.1f}h → {value:.1f}h)"`;
   - Value/change_rel available, not significant: `"Median cycle time is {value:.1f}h ({change_rel×100:+.0f}% vs previous period, not significant)"`;
   - Value but no previous/change_rel: `"Median cycle time is {value:.1f}h (no previous period to compare)"`;
   - Missing value: `"Not enough merged PRs for a reliable cycle time"`.
2. If findings exist: "; the main bottleneck is "+first title with lowercase initial. For review_capacity/review_queue_growth append `" ({waiting_reviewer.share×100:.0f}% of PR time waits on reviewers)"`.
3. Benefit if first finding has what_if and non-null change_rel: `". Capping {stage} at {target:.0f}h would cut median cycle time by about {|change_rel|×100:.0f}%"`.
4. End with a period.

## 12. Snapshot assembly (`insights/analytics/snapshot.py`)

### 12.1 Structure

Follow `06` §4 exactly (top-level §4.1, meta §4.3). Multi-repo per_repo has numeric merged_prs/cycle_time_p50_hours/pickup_p50_hours/waiting_share computed independently per repo; null for one repo. Unimplemented P1 sections are null.

Pure entry: `build_snapshot(dataset: Dataset, *, params: SnapshotParams) -> dict[str, Any]`; return a canonically serializable dictionary with no random/time-dependent fields other than snapshot_id.

### 12.2 Rounding and serialization

- Hours 2 decimals; shares/rates (0–1) 4; ratios 2; counts integers.
- Timestamps YYYY-MM-DDTHH:MM:SSZ.
- Canonical JSON: orjson.dumps(obj, option=orjson.OPT_SORT_KEYS). Explicit list ordering throughout; sets become sorted lists.

### 12.3 IDs and ETags

```text
canonical(x)  = orjson.dumps(x, option=orjson.OPT_SORT_KEYS); sha256(...) uses hexdigest
params        = {"repos": sorted lowercase repository names, "from": ISO date, "to": ISO date,
                 "location_dimension": ..., "directory_depth": ..., "ci_source": ...,
                 "analytics_version": ..., "thresholds_version": ...}
params_hash   = sha256(canonical(params))[:16]
versions_hash = sha256(canonical(meta.data_freshness))[:16]
snapshot_id   = "s_" + sha256(f"{params_hash}|{versions_hash}|{as_of_iso}")[:16]
etag          = '"' + sha256(payload_bytes)[:32] + '"'
```

- meta.data_freshness (`06` §4.3) includes each repo's data_version/covered_since/last_synced_at/last_sync_status, so all enter snapshot identity. Identical parameters/data state/observation time give identical IDs/bytes; any change gives a new ID without rewriting old snapshots.
- ID uses parameters and repository columns only, computable without reading snapshot; Redis/Postgres look up directly (`06` §5.1), no extra index key.
- Location/depth/algorithm/threshold changes trigger full rederivation and data_version increment (`04` §6.5). Until repository derived_key matches current, API returns 202 (`06` §5.1), preventing old facts under new metadata.

## 13. CI metrics (P1, `insights/analytics/ci.py`)

Populate bottleneck_analysis.ci (`06` §4.12) from runs mapped to flow PRs, assigned by created_at:

- queue_p50_minutes: median run_started_at-created_at.
- run_p50_minutes: median completed updated_at-run_started_at.
- rerun_rate: share run_attempt>1; flaky_rerun_rate: share run_attempt>1 and conclusion==success.
- `runs_per_pr_p50`;`coverage` = `time_ledger.ci_coverage`;
- top_workflows: top five by total running minutes, {workflow_name,runs,run_p50_minutes,rerun_rate}.
- All include previous comparison. State-machine CI intervals union mapped [created_at,updated_at), including queue time.

## 14. Drivers(P1,`insights/analytics/drivers.py`)

| Field | Definition |
|---|---|
| assignment | assigned={n,pickup_p50_hours} for M with review_requested_before_first_review; unassigned for others; ratio=unassigned median / assigned median |
| review_round_cost | Buckets "0","1","2","3+": {rounds,n,cycle_p50_hours}; hours_per_extra_round=median adjacent-bucket median differences; re_review_wait_p50_hours=median waiting_reviewer intervals immediately following waiting_author; first_pickup_p50_hours=stage_p50_hours.pickup |
| author_wip | Buckets "0","1-2","3+" by author_open_prs_at_ready: {wip,n,waiting_author_p50_hours} (median PR author-wait totals); spearman=rank Pearson correlation in numpy |
| submit_timing | by_weekday={weekday,n,pickup_p50_hours} for UTC 0–6; by_hour_block={hours,n,pickup_p50_hours} for "00-05","06-11","12-17","18-23" UTC |
| slowest_decile | Slowest ceil(10% of M) by cycle versus rest: {n,features:[{feature,slowest,rest,ratio}]}; features ordered size_lines_p50,external_share,multi_location_share (≥2),review_rounds_p50,unrequested_share; ratio=slowest/rest, None for rest=0; whole item None for count(M)<50 |

Populate snapshot drivers (`06` §4.12); group/bucket medians below ten samples are None, not errors.

## 15. Survival analysis (P1)

- Cohort: flow PRs ready in [from_dt,end_dt), observed at as_of; previous ready cohort observed at from_dt.
- Event time: merged_at-ready_at if merged before observation; otherwise censor at min(observation,closed_at)-ready_at.
- Kaplan–Meier: ascending time, S(t)=Π(1-d_i/n_i); events before censoring at ties.
- KM={n,events,median_hours,s_at_hours}; median first t with S(t)<=0.5, else None; survival at 24/72/168/336 hours with string keys.
- efficiency.survival={current:KM,previous:KM|None} (`06` §4.12); each cohort KM=None below 20 PRs.

## 16. Predictability (P1)

- within_hist_p85: historical flow cycle p85 with ≥30 merges in [from_dt-90 days,from_dt); share of M at/below baseline, ~0.85 when stable. Previous uses M_prev and [prev_from-90 days,prev_from). Load per §6.2 item 8. If baseline start precedes covered_since, value=None, status=insufficient_sample, extra.reason=baseline_not_covered.
- weekly_throughput_cv: population standard deviation / mean of merged counts in complete seven-day weeks; require ≥4 complete weeks; same for previous.
- Both Metric, units share/coefficient, no significance; efficiency.predictability.

## 17. PR detail rows (`insights/analytics/rows.py`)

`build_pr_rows(dataset: Dataset) -> list[dict[str, Any]]` serves /v1/insights/delivery/prs (`06` §7.2); flow PRs only, three populations:

- merged: M.
- closed: Cl.
- open: open at as_of (§2.7).

Row ledger_hours covers waiting states in [ready_at,min(end_at,as_of)]. Open current_state/current_state_age_hours use containing interval. at_risk shares §9.6 candidates (including current-draft exclusion when observed now) and baseline function from the same computation, so non-null row risks count equals at_risk_summary.total. Return all rows; API filters/sorts/paginates per `06` §5.2.

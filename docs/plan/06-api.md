# 06 API contract

This external contract has highest precedence (`AGENTS.md` §2). Field names, status codes, headers, and problem types must match it; Pydantic response models live in insights/api/schemas.py.

## 1. General rules

- Business endpoints use /v1; /healthz and /readyz have no prefix.
- UTF-8 JSON requests/responses, snake_case fields. UTC timestamps YYYY-MM-DDTHH:MM:SSZ; dates YYYY-MM-DD. Rounding per `05` §12.2.
- OpenAPI /openapi.json, interactive /docs; info.title="Delivery Insights API", info.version=insights.__version__.
- API never calls GitHub or imports insights.sources. Reads Postgres/Redis; writes only snapshots/narratives/cache plus manual-sync sync_jobs/arq jobs (`04` §6.8).

| Method and path | Purpose | Status codes | Cache |
|---|---|---|---|
| GET /v1/insights/delivery | Period insight snapshot (Endpoint 1) | 200, 202, 304, 403, 422, 503 | private, max-age=60 + ETag |
| GET /v1/insights/delivery/prs | Filtered/paginated PR stage/waiting detail | 200, 202, 403, 422, 503 | private, max-age=60 |
| GET /v1/snapshots/{snapshot_id} | Immutable snapshot by ID | 200, 304, 404, 422 | private, max-age=86400, immutable + ETag |
| GET /v1/snapshots/{snapshot_id}/narrative | LLM narrative (Endpoint 2) | 200, 304, 404, 422 | See §5.4 |
| GET /v1/repos | Whitelist and sync status | 200 | no-store |
| POST /v1/repos/{owner}/{name}/sync | Request manual synchronization | 202, 403, 422, 429, 503 | no-store |
| GET /v1/sync-jobs/{job_id} | Sync-job status | 200, 404, 422 | no-store |
| GET /healthz | Liveness, no dependency checks | 200 | no-store |
| GET /readyz | Postgres/Redis readiness | 200, 503 | no-store |

Every /v1 endpoint can also return 429 (§2.6) or 500 (§2.3).

## 2. Shared conventions

### 2.1 Request ID

- Reuse X-Request-ID matching ^[A-Za-z0-9._-]{1,64}$; otherwise generate uuid4().hex.
- Always return X-Request-ID; include it in structlog contextvars access logs and problem.request_id.

### 2.2 Serialization

- Snapshot endpoints return canonical bytes (`05` §12.2) directly so ETag matches bytes. Still declare response_model=Snapshot for OpenAPI; FastAPI does not revalidate Response objects. Tests call Snapshot.model_validate.
- Other endpoints use Pydantic serialization with ConfigDict(extra="forbid").
- No GZip middleware: avoid ETag/transport byte mismatch; unnecessary locally.

### 2.3 Errors: problem+json (RFC 9457)

All 4xx/5xx, excluding 304, use Content-Type: application/problem+json:

```json
{
  "type": "/problems/invalid-parameter",
  "title": "Invalid parameter",
  "status": 422,
  "detail": "'from' must not be later than 'to'.",
  "instance": "/v1/insights/delivery",
  "request_id": "6f1c0e8a9b2d4c7e8f0a1b2c3d4e5f60",
  "errors": [{"param": "from", "message": "must not be later than 'to'"}]
}
```

| type | status | title | Use |
|---|---|---|---|
| /problems/invalid-parameter | 422 | Invalid parameter | Invalid query/path/cursor; errors lists each issue |
| /problems/not-tracked | 403 | Repository not tracked | Repository/org outside TRACKED_REPOS; repos extension lists validated untracked names |
| /problems/not-found | 404 | Not found | Missing snapshot/job; unknown route |
| /problems/method-not-allowed | 405 | Method not allowed | Unsupported method |
| /problems/rate-limited | 429 | Too many requests | API rate limit (§2.6), Retry-After |
| /problems/sync-cooldown | 429 | Sync recently requested | Manual-sync cooldown, Retry-After |
| /problems/data-unavailable | 503 | Data unavailable | Period uncovered and sync blocked by missing_token/auth_error/not_found; repos extension lists last_sync_status |
| /problems/dependency-unavailable | 503 | Dependency unavailable | Postgres or Redis unavailable |
| /problems/internal-error | 500 | Internal server error | Unhandled exception; fixed detail "An unexpected error occurred." |

Rules:

- detail/errors[].message **never echo raw parameters**, preventing reflected content/log injection. Only allowlist-validated repository names may appear in extensions.
- Do not expose traces, SQL, upstream bodies, or original exceptions. Server-only exception logging must still exclude headers/full upstream bodies (`02` §4).
- insights/api/errors.py provides ProblemError(status,type_slug,title,detail,errors=None,headers=None,extensions=None) and handlers. Convert RequestValidationError to 422 (errors[].param=last loc component); handle Starlette 404/405 and unhandled exceptions.

### 2.4 Caching and conditional requests

- **Strong ETag**: quote the first 32 hex digits of SHA256(response bytes). Compute once and store in snapshots.etag / Redis hash, then reuse.
- **If-None-Match** supports comma-separated tags, W/ prefix (strip for weak comparison), and *. Matching returns 304 with ETag/Cache-Control/X-Request-ID and insight X-Snapshot-Id/Content-Location only; no body.
- GET /v1/insights/delivery computes snapshot_id first; Redis HGET etag comparison can return 304 without reading body.
- Cache-Control values in §1.

### 2.5 Pagination (PR detail endpoint only)

- limit integer 1–200, default 50; cursor opaque.
- Response: {"items":[...],"next_cursor":"…" or null,"total":123,...}.
- Cursor: unpadded base64url of orjson.dumps({"v":1,"sid":snapshot_id,"o":offset,"f":filters_hash}); filters_hash=first 12 SHA256 hex digits of canonical filters.
- Validate in order: ^[A-Za-z0-9_-]{1,512}$ → base64url decode → JSON object → keys/types → o>=0. Any failure is 422 with param="cursor".
- sid differs from current snapshot (data changed), or f differs from current filters: 422, message="cursor is no longer valid; restart from the first page".

### 2.6 Rate limiting

- /v1/* only, fixed window: INCR di:rl:{client_ip}:{epoch_minute}, EXPIRE 70 on first count. Above RATE_LIMIT_PER_MINUTE return 429 /problems/rate-limited, Retry-After seconds until next minute, minimum one.
- Every /v1 response includes X-RateLimit-Limit and X-RateLimit-Remaining.
- client_ip=request.client.host. Do not parse X-Forwarded-For manually. Uvicorn --proxy-headers trusts only 127.0.0.1 by default; frontend nginx requests share its IP/bucket. Acceptable local trade-off; disclose in README.
- Redis failure **fails open**, with warning; limiter failure must not reject service.

### 2.7 CORS and security headers

- CORS: CORS_ORIGINS only; GET/POST; allow If-None-Match/Content-Type/X-Request-ID; expose ETag/Retry-After/Location/Content-Location/X-Request-ID/X-Snapshot-Id; allow_credentials=False.
- All responses include X-Content-Type-Options: nosniff.

### 2.8 Dependency failures

| Failure | Behavior |
|---|---|
| Postgres connection/timeout failure | 503 /problems/dependency-unavailable |
| Redis cache reads/writes | Warn, bypass cache, compute/read Postgres |
| Redis rate limiting | Fail open (§2.6) |
| Redis manual-sync enqueue | 503 /problems/dependency-unavailable |
| GitHub unavailable | API unaffected; meta.data_freshness shows last sync time/status |
| Bedrock unavailable | Template fallback, still 200 (`07` §9) |

## 3. Parameters

### 3.1 Repositories and organizations

```python
REPO_RE  = r"^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/(?P<name>(?!\.{1,2}$)[A-Za-z0-9._-]{1,100})$"
OWNER_RE = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"
NAME_RE  = r"^(?!\.{1,2}$)[A-Za-z0-9._-]{1,100}$"
```

- Repeatable repo (?repo=a/b&repo=c/d); each matches REPO_RE. Lowercase dedup, preserve first request occurrence.
- org matches OWNER_RE; resolve TRACKED_REPOS with same owner case-insensitively.
- **Exactly one** of repo or org is required; otherwise 422.
- More than MAX_REPOS_PER_REQUEST resolved repositories: 422.
- Whitelist is configuration TRACKED_REPOS, independent of DB row existence. Any untracked repo or org without tracked repos: 403 /problems/not-tracked.
- owner/name path fields match OWNER_RE/NAME_RE.
- Responses/snapshots use TRACKED_REPOS display casing.

### 3.2 Dates

- Strict ^\d{4}-\d{2}-\d{2}$ then date.fromisoformat.
- Defaults: to=today UTC, from=to-29 days (30-day window).
- Collect all violations in errors: from<=to; inclusive length<=366; to<=today UTC; from>=today-BACKFILL_DAYS (earlier data is not synchronized).
- Previous comparison is adjacent same-length window (`05` §6.1), not a request parameter.

### 3.3 Other parameters

| Parameter | Endpoint | Values | Default |
|---|---|---|---|
| `status` | `/prs` | `merged`, `closed`, `open` | `merged` |
| at_risk | /prs | true, false | false; true requires status=open or omitted, interpreted as open |
| state | /prs | waiting_reviewer, waiting_author, waiting_ci, waiting_merge | None; requires status=open or at_risk=true, else 422 |
| location | /prs | ^[^\x00-\x1f\x7f]{1,200}$; exact PR locations match | None |
| `limit`, `cursor` | `/prs` | §2.5 | |
| `audience` | narrative | `director`, `manager` | `manager` |
| `lang` | narrative | `en` only; other values return 422 | `en` |
| snapshot_id | Path | ^s_[0-9a-f]{16}$ | |
| job_id | Path | Canonical lowercase hyphenated UUID | |

Ignore unknown query parameters.

## 4. Snapshot structure (`Snapshot`)

Pure functions in 05 compute this structure. Unless specified, previous means period.compared_to; comparison_available=false makes all previous fields null. List sorting follows relevant 05 sections.

### 4.1 Top level

| Field | Type | Description |
|---|---|---|
| `snapshot_id` | string | `05` §12.3 |
| repos | string[] | Sorted display names |
| period | object | {from,to,days,complete,compared_to:{from,to}}; complete=(as_of==to_excl). False means requested end is not yet observed (`05` §6.1). to=today always incomplete because today has not ended |
| `as_of` | timestamp | `05` §6.1 |
| `headline` | string | `05` §11 |
| `efficiency` | object | §4.4 |
| `time_ledger` | object | §4.5 |
| bottlenecks | Finding[] | Sorted findings (§4.6); empty with fewer than 20 current merged PRs (`05` §11) |
| `bottleneck_analysis` | object | §4.7 |
| drivers | object \| null | P1 §4.12; null P0 |
| at_risk_prs | AtRiskPr[] | §4.8, capped at AT_RISK_MAX_ITEMS |
| `at_risk_summary` | object | §4.8 |
| `waste`, `rework`, `guardrail` | object | §4.9 |
| `trend` | object | §4.10 |
| `signals` | object | §4.11 |
| `series` | object | §4.11 |
| per_repo | object[] \| null | Null for one repository; §4.11 |
| `links` | object | `{"self": "/v1/snapshots/{id}", "narrative": "/v1/snapshots/{id}/narrative"}` |
| `meta` | object | §4.3 |

### 4.2 Metric object

All metrics share one structure, computed identically for current/previous:

```json
{
  "value": 41.25,
  "unit": "hours",
  "n": 512,
  "previous": 35.1,
  "n_previous": 498,
  "change_abs": 6.15,
  "change_rel": 0.1752,
  "significant": true,
  "status": "ok",
  "extra": {}
}
```

| Field | Description |
|---|---|
| value | Current value; null below sample gate |
| unit | hours, minutes, count, share (0–1), ratio, lines, rounds, coefficient, change (relative; 0.25=+25%) |
| n, n_previous | Sample sizes; rate denominator |
| previous | Previous value; null if unavailable/insufficient |
| change_abs | value-previous; null if either missing |
| change_rel | change_abs/previous; null if previous zero/missing |
| significant | 05 §10; null if not computed, inapplicable, no comparison, or insufficient sample |
| status | ok or insufficient_sample when value null |
| extra | Metric-specific, e.g. {k:2}, {n_days:3}, {events:3,denominator:41}; {} if none |

### 4.3 `meta`

```json
{
  "schema_version": "1",
  "analytics_version": "1.0.0",
  "thresholds_version": "1.0.0",
  "location_dimension": "label:area-",
  "time_basis": "utc_wall_clock",
  "comparison_available": true,
  "ci_source": "actions",
  "data_freshness": [
    {"repo": "dotnet/runtime", "data_version": 1234, "covered_since": "2026-04-05T09:00:00Z",
     "last_synced_at": "2026-10-02T09:45:12Z", "last_sync_status": "ok"}
  ],
  "sample": {"merged_prs": 812, "closed_unmerged_prs": 141, "ready_prs": 968,
             "open_prs_at_as_of": 655, "human_reviews": 3120},
  "excluded": {"bot_prs": 214, "backport_prs": 97, "never_ready_drafts": 23},
  "location_sources": {"label": 702, "codeowners": 0, "directory": 96, "unclassified": 14}
}
```

- data_freshness: one per repository sorted by name; also versions_hash input (`05` §12.3).
- sample: merged_prs=current merged flow count; closed_unmerged_prs=current closed unmerged flow count; ready_prs=ready in current period; open_prs_at_as_of=open at observation (`05` §2.7, excluding temporary closure); human_reviews=current flow human reviews.
- excluded: current merged/closed bot/backport and current closed never-ready drafts (`05` §4.1); one class per PR, bot → backport → never_ready_draft (`05` §6.2 item 5).
- location_sources: current merged flow PR counts by location_source.
- ci_source: configured CI_SOURCE.

### 4.4 `efficiency`

Keys correspond to `05` §7; values are Metric:

```json
{
  "merged_prs": Metric, "effective_throughput": Metric,
  "cycle_time_p50_hours": Metric, "cycle_time_p90_hours": Metric,
  "stage_p50_hours": {"coding": Metric, "pickup": Metric, "review": Metric, "merge": Metric},
  "merged_within_n_days": Metric, "waiting_share": Metric, "waste_share": Metric,
  "avg_review_rounds": Metric, "post_review_commit_share": Metric,
  "review_concentration_top_k": Metric, "revert_rate": Metric, "pr_size_p50_lines": Metric,
  "predictability": null, "survival": null
}
```

predictability/survival populated in P1 M9 (§4.12).

### 4.5 `time_ledger`

```json
{
  "scope": "merged_prs",
  "merged_prs": 812, "previous_merged_prs": 798,
  "total_pr_hours": 29012.5, "previous_total_pr_hours": 25110.0,
  "states": {
    "waiting_reviewer": {"pr_hours": 12185.3, "previous_pr_hours": 8790.1, "share": 0.42, "previous_share": 0.3501, "change_pp": 6.99},
    "waiting_author":   {"pr_hours": 9284.0, "previous_pr_hours": 9541.8, "share": 0.32, "previous_share": 0.38, "change_pp": -6.0},
    "waiting_ci":       {"pr_hours": 0.0, "previous_pr_hours": 0.0, "share": 0.0, "previous_share": 0.0, "change_pp": 0.0},
    "waiting_merge":    {"pr_hours": 7543.2, "previous_pr_hours": 6778.1, "share": 0.26, "previous_share": 0.2699, "change_pp": -0.99}
  },
  "ci_coverage": 0.0,
  "ci_data_available": false
}
```

Definitions: `05` §8. change_pp rounded to two decimals.

### 4.6 `bottlenecks`(Finding)

```json
{
  "id": "review_capacity:area-System.Net.Http",
  "rank": 1,
  "type": "review_capacity",
  "severity": "medium",
  "title": "First-review wait concentrated in area-System.Net.Http",
  "location": "area-System.Net.Http",
  "impact_pr_hours": 2460.5,
  "impact_share": 0.0848,
  "evidence": [
    {"label": "First-review wait vs rest of repo", "value": 2.4, "unit": "ratio",
     "ref": "/bottleneck_analysis/locations/0/pickup_ratio_vs_rest"},
    {"label": "Share of reviewer-waiting time", "value": 0.2019, "unit": "share",
     "ref": "/bottleneck_analysis/locations/0/waiting_reviewer_share"}
  ],
  "recommendation": "Add reviewers or code owners for area-System.Net.Http, enable team auto-assignment, and set a one-business-day first-review SLA.",
  "what_if": {"stage": "pickup", "location": "area-System.Net.Http", "target_hours": 8.0, "affected_prs": 37,
              "cycle_p50_before_hours": 41.25, "cycle_p50_after_hours": 36.9, "change_rel": -0.1055}
}
```

- type in review_capacity, review_queue_growth, review_concentration, merge_blocked, ci_wait (P1), rework_high, waste_high, quality_guardrail, external_contributor_wait; `05` §11 rules.
- severity high/medium/low; location null if absent; impact_share=impact_pr_hours/time_ledger.total_pr_hours, zero for zero total.
- evidence[].ref is RFC 6901 JSON Pointer into this snapshot; Metric refs point to the object.
- what_if uses §4.7 structure; null if absent.

### 4.7 `bottleneck_analysis`

```json
{
  "review_queue": {
    "weeks": [{"week_start": "2026-09-03", "days": 4, "inflow": 120, "outflow": 98, "open_at_week_end": 410}],
    "weeks_total": 5,
    "weeks_inflow_exceeds_outflow": 4,
    "open_growth_rel": 0.2195,
    "net_inflow_share": 0.20
  },
  "locations": [
    {"location": "area-System.Net.Http", "merged_prs": 41, "pickup_p50_hours": 30.5, "pickup_ratio_vs_rest": 2.4,
     "waiting_reviewer_pr_hours": 2460.5, "previous_waiting_reviewer_pr_hours": 1102.0, "waiting_reviewer_share": 0.2019,
     "inflow": 52, "outflow": 40, "at_risk_prs": 6, "owners_count": null}
  ],
  "merge_blockers": {"approved_merged_prs": 640, "second_approval_share": 0.31, "second_approval_wait_p50_hours": 5.2,
                     "post_approval_update_share": 0.22, "ci_after_approval_p50_hours": null},
  "pareto": [{"cause": "waiting_reviewer", "location": "area-System.Net.Http", "pr_hours": 2460.5, "share": 0.0848}],
  "what_if": [{"stage": "pickup", "location": null, "target_hours": 8.0, "affected_prs": 301,
               "cycle_p50_before_hours": 41.25, "cycle_p50_after_hours": 33.1, "change_rel": -0.1976}],
  "review_load": {"reviewers": 85, "reviews": 3120,
                  "distribution": [{"reviewer": "octocat", "reviews": 240, "share": 0.0769}]},
  "ci": null
}
```

- review_queue per `05` §9.2: net_inflow_share=(Σinflow−Σoutflow)/Σinflow, null with no inflow, may be negative. Stock/growth are period-cohort display only, never trigger findings. Locations §9.1; merge_blockers §9.3; pareto §9.4 (location only for waiting_reviewer); what_if §9.5 whole-repo items (location=null); review_load §9.8; CI P1 §4.12 here.

### 4.8 `at_risk_prs`, `at_risk_summary`

```json
{
  "repo": "dotnet/runtime", "number": 108123, "title": "…", "url": "https://github.com/dotnet/runtime/pull/108123",
  "author": "octocat", "state": "waiting_reviewer", "age_hours": 212.4,
  "threshold_hours": 70.1, "critical_threshold_hours": 160.3, "severity": "critical",
  "baseline_source": "90d", "locations": ["area-System.Net.Http"], "external_contributor": true, "size_lines": 84
}
```

author is string or null for deleted accounts. at_risk_summary={"total":61,"critical":14,"by_state":{"waiting_reviewer":38,"waiting_author":15,"waiting_ci":0,"waiting_merge":8}}; always include all four states. Definitions `05` §9.6.

### 4.9 `waste`, `rework`, `guardrail`

```json
"waste": {"closed_unmerged": 141, "by_class": {"superseded": 30, "rejected": 41, "abandoned": 38, "no_review": 32},
          "lost_while_waiting": 44, "late_rejections": 9, "wasted_review_share": 0.071, "wasted_pr_hours": 5210.4},
"rework": {"reverts": 6, "revert_prs": 6, "relanded": 3,
           "revert_chains": [{"original": {"number": 107001, "url": "…"}, "revert": {"number": 107050, "url": "…"},
                              "reland": null, "exposure_hours": 30.2, "revert_pr_cycle_hours": 2.1}]},
"guardrail": {"cycle_time_p50_change_rel": 0.1752, "revert_rate": 0.0074, "previous_revert_rate": 0.005,
              "revert_rate_change_pp": 0.24, "verdict": "ok"}
```

Definitions `05` §9.9. revert_prs=current merged revert count; verdict in ok/watch/tradeoff_suspected.

### 4.10 `trend`

```json
{
  "bottleneck_shift": "waiting_reviewer share +7.0pp vs previous period",
  "attribution": {
    "basis": "mean_hours_per_merged_pr",
    "cycle_mean_hours": {"current": 57.83, "previous": 52.47, "change": 5.36},
    "total_increase_hours": 5.89,
    "total_decrease_hours": 0.52,
    "states": {
      "coding":           {"current": 22.1, "previous": 21.0, "change": 1.1, "share_of_increase": 0.1868, "share_of_decrease": 0.0},
      "waiting_reviewer": {"current": 15.01, "previous": 11.02, "change": 3.99, "share_of_increase": 0.678, "share_of_decrease": 0.0},
      "waiting_author":   {"current": 11.43, "previous": 11.96, "change": -0.52, "share_of_increase": 0.0, "share_of_decrease": 1.0}
    },
    "locations": [{"location": "area-System.Net.Http", "change": 1.65, "share_of_reviewer_increase": 0.3946, "share_of_increase": 0.2675}],
    "large_prs": {"current": 14.2, "previous": 10.0, "change": 4.2, "share_of_increase": 0.7831}
  }
}
```

states includes coding and all four waiting states (three shown). Consistent with §4.5/§4.7: current/previous waiting means divide ledger hours by 812/798 merged PRs; location change=2460.5/812−1102.0/798. Assume positive location gross=4.18, at least reviewer growth 3.99 because locations partition reviewer waiting; share_of_reviewer_increase=1.65/4.18, share_of_increase=0.678×0.3946. Example cycle_mean_hours equals component sums; real closures/missing commits can cause differences. `05` §9.7/§9.12; attribution=null when unavailable.

### 4.11 `signals`, `series`, `per_repo`

- signals: {large_pr_share,merged_without_approval_share,fast_large_approval_share,external_pickup_ratio,at_risk_reviewer_top_location_share}, all Metric (`05` §9.10).
- series={current:Week[],previous:Week[]}; Week={week_start,days,merged,cycle_p50_hours,pickup_p50_hours,pr_size_p50_lines,waiting_reviewer_share,waiting_ci_share,reverts} (`05` §9.11); previous=[] if unavailable.
- per_repo: multi-repo entries {repo,merged_prs,cycle_time_p50_hours,pickup_p50_hours,waiting_share}; numeric/null values, not Metric; sorted by repository.

### 4.12 P1 fields

| Path | Structure | Source |
|---|---|---|
| `bottleneck_analysis.ci` | `{"source", "coverage", "queue_p50_minutes": Metric, "run_p50_minutes": Metric, "rerun_rate": Metric, "flaky_rerun_rate": Metric, "runs_per_pr_p50": Metric, "top_workflows": [{"workflow_name", "runs", "run_p50_minutes", "rerun_rate"}]}` | `05` §13 |
| bottleneck_analysis.locations[].owners_count | Integer or null | 05 §9.1 |
| `drivers` | `{"assignment", "review_round_cost", "author_wip", "submit_timing", "slowest_decile"}` | `05` §14 |
| `efficiency.predictability` | `{"within_hist_p85": Metric, "weekly_throughput_cv": Metric}` | `05` §16 |
| `efficiency.survival` | `{"current": KM, "previous": KM \| null}`,`KM = {"n", "events", "median_hours", "s_at_hours": {"24", "72", "168", "336"}}` | `05` §15 |

drivers substructures:

```json
{
  "assignment": {"assigned": {"n": 512, "pickup_p50_hours": 14.0}, "unassigned": {"n": 300, "pickup_p50_hours": 31.0}, "ratio": 2.21},
  "review_round_cost": {"buckets": [{"rounds": "0", "n": 210, "cycle_p50_hours": 20.1}, {"rounds": "1", "n": 330, "cycle_p50_hours": 38.0},
                                    {"rounds": "2", "n": 160, "cycle_p50_hours": 61.2}, {"rounds": "3+", "n": 112, "cycle_p50_hours": 99.0}],
                        "hours_per_extra_round": 23.1, "re_review_wait_p50_hours": 18.0, "first_pickup_p50_hours": 14.6},
  "author_wip": {"buckets": [{"wip": "0", "n": 300, "waiting_author_p50_hours": 10.0}, {"wip": "1-2", "n": 400, "waiting_author_p50_hours": 14.0},
                             {"wip": "3+", "n": 112, "waiting_author_p50_hours": 25.0}], "spearman": 0.21},
  "submit_timing": {"by_weekday": [{"weekday": 0, "n": 140, "pickup_p50_hours": 12.0}],
                    "by_hour_block": [{"hours": "00-05", "n": 90, "pickup_p50_hours": 20.0}]},
  "slowest_decile": {"n": 81, "features": [{"feature": "size_lines_p50", "slowest": 610.0, "rest": 90.0, "ratio": 6.78}]}
}
```

slowest_decile.features[].feature in size_lines_p50/external_share/multi_location_share/review_rounds_p50/unrequested_share; insufficient values null.

### 4.13 Complete example

backend/tests/golden/snapshot_seed42.json is authoritative (generated M5). Include a truncated real example in README M12.

## 5. Endpoint details

### 5.1 `GET /v1/insights/delivery`

Parameters: repeatable repo or org; from/to.

Flow in insights/snapshot_service.py, shared with worker precompute:

1. Parse/validate parameters (§3) → 422.
2. Resolve repositories/whitelist → 403/422.
3. Open REPEATABLE READ read-only transaction for all DB reads in steps 3–9, shared with dataset loading (`05` §6.2), ensuring identity/content consistency. Persist snapshots in a separate short transaction. Read repository rows; absent tracked repo means never synced, covered_since=null, last_sync_status=never, data_version=0.
4. **Readiness**: compute as_of=min(to_excl,minimum last_synced_at) (`05` §6.1). Check each repository in order; first failure determines reason:

   | Condition | Failure reason |
   |---|---|
   | covered_since and last_synced_at non-null | never_synced |
   | `covered_since <= from_dt` | `backfill` |
   | last_open_sweep_at non-null | open_sweep |
   | derived_key equals current insights.analytics.derive_key(...), shared by worker/API (`04` §6.2) | rederive |
   | last_synced_at>from_dt, guaranteeing as_of>from_dt | stale |

   First three conditions become satisfied together after first backfill stage (`04` §6.3). Version/location changes require completed rederivation before new-config snapshots. If not ready:
   - Any not-ready reason needing GitHub (never_synced/backfill/open_sweep/stale) with status missing_token/auth_error/not_found → 503 /problems/data-unavailable. rederive needs no GitHub/token; return 202 instead.
   - Otherwise **202** (§7.1), Retry-After:30. Location points to first unready repository's latest queued/running job: rederive for that reason, sync otherwise; omit if none. GET **never enqueues**; worker startup/schedule continues backfill.
5. Compute data_freshness, params_hash, versions_hash, snapshot_id (`05` §12.3).
6. Conditional fast path: Redis HMGET di:snap:{id} etag created_at, logically unexpired (`03` §2.12), matching If-None-Match → 304.
7. Unexpired Redis HGETALL → 200. Logically expired hit counts as miss; continue step 8, overwritten by step 9.
8. Unexpired Postgres snapshot (`03` §2.12) → canonical bytes/stored ETag → Redis refill with min(24h,remaining logical lifetime) → 200, checking If-None-Match again.
9. Miss, including expired DB row: async dataset.load_dataset → asyncio.to_thread(build_snapshot,...) → canonical bytes/ETag → INSERT with created_at=injected now, ON CONFLICT(snapshot_id) DO UPDATE SET created_at=EXCLUDED.created_at (identity fixes content; renew seven-day readability) → Redis HSET including created_at + EXPIRE 86400 → 200. Log snapshot_computed with snapshot_id/duration_ms/load_ms/compute_ms/merged_prs. Concurrent cold requests may compute identically twice; ON CONFLICT deduplicates, no lock; record trade-off in DECISIONS.

200 headers: ETag, Cache-Control: private, max-age=60, Content-Location: /v1/snapshots/{id}, X-Snapshot-Id.

### 5.2 `GET /v1/insights/delivery/prs`

Parameters: §5.1 repo/org/from/to plus status/at_risk/state/location/limit/cursor (§3.3).

1. Same first five steps as §5.1, including 202/403/422/503.
2. Rows: Redis di:rows:{snapshot_id}, orjson list, one-hour TTL. Miss: load dataset, pure analytics/rows.py build_pr_rows, cache all rows.
3. Population per `05` §17: merged=current merged, closed=current closed unmerged, open=open at as_of (`05` §2.7: ready, unended, not temporarily closed; no never-ready drafts). at_risk=true keeps non-null at_risk; filter current waiting state and location. Flow PRs only (`05` §4.1).
4. Fixed sort: merged descending cycle_hours, closed descending closed_at, open descending current_state_age_hours, risks descending age_hours/threshold_hours; ties ascending (repo,number).
5. Paginate (§2.5); response §7.2.

### 5.3 `GET /v1/snapshots/{snapshot_id}`

Read Redis → Postgres; missing/housekeeping-deleted/logically older than seven days returns 404 even if Redis TTL remains (`03` §2.12). ETag, Cache-Control: private, max-age=86400, immutable; support 304.

### 5.4 `GET /v1/snapshots/{snapshot_id}/narrative`

Parameters audience/lang; narratives are English only and lang accepts only en; flow `07` §9, response §6.

- Missing snapshot: 404, including narrative foreign-key race after cleanup.
- ETag; persistent narratives (LLM or unconfigured-Bedrock templates): private, max-age=3600. Temporary LLM-failure templates: no-store. Support 304.
- LLM/validation failures return 200 template, not 5xx. Postgres failure returns 503.

### 5.5 `GET /v1/repos`

Return {items:[RepoStatus]} (§7.3) for every TRACKED_REPOS entry in configured order. Absent DB row has null fields, last_sync_status=never, data_version=0.

### 5.6 `POST /v1/repos/{owner}/{name}/sync`

1. Validate path (422), whitelist (403).
2. Cooldown: SET di:cooldown:sync:{repo_lower} 1 NX EX MANUAL_SYNC_COOLDOWN_SECONDS; existing → 429 sync-cooldown, Retry-After=remaining TTL, minimum one.
3. Shared enqueue_sync(...,kind="manual") (`04` §6.8). Lifespan creates arq pool using arq.create_pool(RedisSettings.from_dsn(REDIS_URL)), injected via get_arq:
   - New job → 202, new queued SyncJob.
   - Repository already syncing/queued → 202, existing job, no new one.
4. Location: /v1/sync-jobs/{id}, Cache-Control:no-store; Redis unavailable → 503.

### 5.7 `GET /v1/sync-jobs/{job_id}`

Return SyncJob (§7.4); missing 404; invalid UUID 422.

### 5.8 `GET /healthz`, `GET /readyz`

- /healthz: {"status":"ok"}, no dependencies.
- /readyz: SELECT 1 and Redis PING, each two-second timeout. Both pass: {status:ready,checks:{postgres:ok,redis:ok}}; failure 503 dependency-unavailable with checks ok/error, no original exceptions.
- Both exempt from rate limits/access logging to avoid health-check noise.

## 6. Narrative response (`Narrative`)

```json
{
  "snapshot_id": "s_3f9a0c1d2e4b5a67",
  "audience": "manager",
  "lang": "en",
  "narrative": "Median cycle time rose 18% to 41.3h [E1]. …",
  "abstained": false,
  "abstain_reason": null,
  "hypotheses": [
    {
      "id": "H_review_capacity",
      "source": "library",
      "title": "Limited review capacity",
      "location": "area-System.Net.Http",
      "statement": "Limited review capacity in area-System.Net.Http is likely the main cause of the slower cycle time [E1][E15][E53].",
      "confidence": 0.79,
      "confidence_level": "high",
      "confidence_basis": {
        "signal_agreement": 0.8, "signals_present": 4, "signals_total": 5,
        "effect_size": 1.0, "persistence": 0.8, "weeks_holding": "4/5",
        "sample_adequacy": 1.0, "sample_size": 790,
        "localization": 0.27, "counter_evidence": 0, "covers_both_parts": true,
        "raw_score": 0.79, "cap": null, "cap_reason": null, "llm_downgrade": null
      },
      "evidence_chain": [
        {"step": "symptom", "evidence": ["E1", "E18"]},
        {"step": "stage", "evidence": ["E15", "E37"]},
        {"step": "location", "evidence": ["E51", "E53"]},
        {"step": "mechanism", "evidence": ["E22", "E26"]}
      ],
      "counter_evidence": [],
      "alternatives_ruled_out": [{"hypothesis": "H_pr_size_growth", "evidence": ["E30", "E31"]}],
      "alternatives_open": [{"hypothesis": "H_ci_bottleneck", "reason": "no_data"}],
      "action": "Add reviewers or code owners for area-System.Net.Http and enable team auto-assignment.",
      "verify_next": "Two weeks after adding reviewers, check whether the first-review wait in area-System.Net.Http has dropped."
    }
  ],
  "evidence": [
    {"id": "E1", "key": "cycle_time_p50", "label": "Median cycle time", "unit": "hours",
     "value": 41.25, "previous": 35.1, "change_abs": 6.15, "change_rel": 0.1752, "change_pp": null,
     "significant": true, "n": 512, "side": "efficiency", "baseline": "previous_period", "location": null,
     "ref": "/efficiency/cycle_time_p50_hours", "examples": []}
  ],
  "links": {"snapshot": "/v1/snapshots/s_3f9a0c1d2e4b5a67"},
  "meta": {
    "generated_by": "llm",
    "model": "us.anthropic.claude-sonnet-4-6",
    "prompt_version": "v6",
    "validation": "passed",
    "attempts": 1,
    "fallback_reason": null,
    "violations": [],
    "confidence_method": "deterministic-v1",
    "pack_hash": "9c1d0e2f3a4b5c6d",
    "generated_at": "2026-10-02T09:47:03Z"
  }
}
```

| Field | Description |
|---|---|
| narrative | Plain text with [E#] citations |
| abstained, abstain_reason | true if no hypothesis clears gates; no_comparison, no_slowdown (comparable period, no slowdown symptom) or insufficient_signal (`07` §4.2) |
| hypotheses[] | After assembly/LLM downgrades, descending final confidence, library before LLM on ties, then ascending ID. Alternatives consider omitted library hypotheses with at least one symptom: ruled_out only with counter-evidence or all assessable mechanisms absent; otherwise alternatives_open [{hypothesis,reason}], reason=no_data/insufficient_sample/below_threshold/not_selected (`07` §3.3 priority); H_llm alternatives empty. source=library/llm; confidence_level=high/medium/low; chain steps symptom/stage/location/mechanism (LLM hypothesis has one cited step). Code templates supply action/verify_next; null for LLM. Definitions `07` §3–§4 |
| evidence[] | All evidence cited by narrative/hypotheses/chains/counter-evidence/alternatives, ascending numeric ID; JSON Pointer ref; up to three PR example links (`07` §2.1) |
| meta.generated_by | llm or template |
| meta.model | Bedrock inference-profile ID; template for fallback |
| meta.validation | passed=final LLM output valid; failed=both attempts invalid and fallback; not_run=not called or call failed |
| meta.attempts | Actual LLM calls, 0–2 |
| `meta.fallback_reason` | `null`, `llm_disabled`, `llm_error`, `validation_failed`, `llm_busy` |
| meta.violations | Codes such as V5:number_not_in_evidence; no original LLM text |
| meta.pack_hash | First 16 hash digits of canonical evidence pack; narrative identity automatically changes with evidence/scoring configuration (`07` §9.2) |

## 7. Other response structures

### 7.1 Pending(202)

```json
{
  "status": "pending",
  "detail": "Data for the requested period is still being synced.",
  "retry_after_seconds": 30,
  "repos": [
    {"repo": "dotnet/runtime", "covered_since": "2026-09-25T10:00:00Z", "required_since": "2026-09-03T00:00:00Z",
     "open_sweep_done": true, "last_sync_status": "ok", "reason": "backfill",
     "job": {"id": "0f8c…", "status": "running", "phase": "backfill:30d", "url": "/v1/sync-jobs/0f8c…"}}
  ]
}
```

repos lists unready repositories only; reasons never_synced/backfill/open_sweep/rederive/stale (§5.1 step 4). job uses Location selection: latest queued/running rederive for rederive, synchronization job otherwise; null if none.

### 7.2 PR details (`PrPage`, `PrRow`)

```json
{
  "snapshot_id": "s_3f9a0c1d2e4b5a67",
  "as_of": "2026-10-02T09:45:12Z",
  "status": "merged",
  "total": 812,
  "items": [
    {
      "repo": "dotnet/runtime", "number": 108001, "title": "…", "url": "https://github.com/dotnet/runtime/pull/108001",
      "author": "octocat", "status": "merged",
      "created_at": "…", "ready_at": "…", "merged_at": "…", "closed_at": null,
      "size_lines": 120, "size_bucket": "M", "locations": ["area-System.Net.Http"], "external_contributor": false,
      "is_revert": false, "reverted": false, "close_class": null, "review_rounds": 1, "human_reviews": 3,
      "cycle_hours": 52.1,
      "stage_hours": {"coding": 10.2, "pickup": 20.5, "review": 15.0, "merge": 6.4},
      "ledger_hours": {"waiting_reviewer": 25.0, "waiting_author": 10.5, "waiting_ci": 0.0, "waiting_merge": 6.4},
      "current_state": null, "current_state_age_hours": null, "at_risk": null
    }
  ],
  "next_cursor": "eyJ2IjoxLCJzaWQiOi…"
}
```

- author: string or null for deleted accounts.
- ledger_hours: waiting durations in [ready_at,min(end_at,as_of)]; excludes coding, available under stage_hours.coding.
- current_state/current_state_age_hours only for open rows, at as_of.
- at_risk={severity,threshold_hours,critical_threshold_hours,baseline_source} for risks; otherwise null.
- reverted: non-null reverted_at before as_of.

### 7.3 `RepoStatus`

```json
{
  "repo": "dotnet/runtime", "default_branch": "main",
  "covered_since": "2026-04-05T09:00:00Z", "backfill_target_days": 180, "backfill_complete": true,
  "sync_watermark": "2026-10-02T09:40:00Z", "last_synced_at": "2026-10-02T09:45:12Z",
  "last_open_sweep_at": "2026-10-02T09:30:00Z", "last_sync_status": "ok", "last_sync_error": null,
  "data_version": 1234, "latest_job": SyncJob | null
}
```

backfill_complete = covered_since non-null and <=now-BACKFILL_DAYS days.

### 7.4 `SyncJob`

```json
{
  "id": "0f8c2a3e-…", "repo": "dotnet/runtime", "kind": "manual", "status": "running", "phase": "incremental",
  "stats": {"prs_fetched": 75, "prs_changed": 12, "events": 640, "pages": 3, "graphql_cost": 4},
  "error": null, "created_at": "…", "started_at": "…", "finished_at": null,
  "url": "/v1/sync-jobs/0f8c2a3e-…"
}
```

## 8. curl examples (execute every example for M6 DoD)

```bash
API=http://localhost:8000
TO=$(python3 -c 'import datetime as d; print(d.datetime.now(d.timezone.utc).date())')
FROM=$(python3 -c 'import datetime as d; print(d.datetime.now(d.timezone.utc).date() - d.timedelta(days=29))')

# 1. Repository/sync status → 200
curl -s "$API/v1/repos" | python3 -m json.tool

# 2. Last-30-day insight → 200; unfinished backfill 202 with Retry-After/Location
curl -si "$API/v1/insights/delivery?repo=dotnet/runtime&from=$FROM&to=$TO" | head -20

# 3. Conditional GET → 304; inspect headers only (GET routes do not handle HEAD)
ETAG=$(curl -s -D - -o /dev/null "$API/v1/insights/delivery?repo=dotnet/runtime&from=$FROM&to=$TO" | awk -F': ' 'tolower($1)=="etag"{print $2}' | tr -d '\r')
curl -s -o /dev/null -w '%{http_code}\n' -H "If-None-Match: $ETAG" "$API/v1/insights/delivery?repo=dotnet/runtime&from=$FROM&to=$TO"

# 4. Invalid parameter → 422 problem+json, no raw input echo
curl -s "$API/v1/insights/delivery?repo=../../etc&from=$FROM&to=$TO"
curl -s "$API/v1/insights/delivery?repo=dotnet/runtime&from=$TO&to=$FROM"
curl -s "$API/v1/insights/delivery?repo=dotnet/runtime&org=dotnet"

# 5. Outside whitelist → 403
curl -s -o /dev/null -w '%{http_code}\n' "$API/v1/insights/delivery?repo=torvalds/linux&from=$FROM&to=$TO"

# 6. Snapshot by ID → 200, immutable Cache-Control
SID=$(curl -s "$API/v1/insights/delivery?repo=dotnet/runtime&from=$FROM&to=$TO" | python3 -c 'import sys,json; print(json.load(sys.stdin)["snapshot_id"])')
curl -s -D - -o /dev/null "$API/v1/snapshots/$SID" | grep -i -E '^(etag|cache-control)'

# 7. At-risk details → 200
curl -s "$API/v1/insights/delivery/prs?repo=dotnet/runtime&from=$FROM&to=$TO&at_risk=true&limit=5" | python3 -m json.tool

# 8. Manual sync → 202; immediate repeat → 429
curl -si -X POST "$API/v1/repos/dotnet/runtime/sync" | head -5
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$API/v1/repos/dotnet/runtime/sync"

# 9. Narrative → 200; unconfigured Bedrock gives meta.generated_by == "template"
curl -s "$API/v1/snapshots/$SID/narrative?audience=director&lang=en" | python3 -m json.tool
```

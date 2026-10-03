"""Explicit public snapshot contract; unexpected fields fail validation."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from insights.analytics.snapshot import iso

State = Literal["waiting_reviewer", "waiting_author", "waiting_ci", "waiting_merge"]
Unit = Literal[
    "hours", "minutes", "count", "share", "ratio", "lines", "rounds", "coefficient", "change"
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True)

    @field_serializer("*", when_used="json")
    def serialize_timestamp(self, value: Any) -> Any:
        return iso(value) if isinstance(value, datetime) else value


class Metric(Contract):
    value: float | int | None
    unit: Unit
    n: int
    previous: float | int | None
    n_previous: int | None
    change_abs: float | None
    change_rel: float | None
    significant: bool | None
    status: Literal["ok", "insufficient_sample"]
    extra: dict[str, Any]


class DateRange(Contract):
    start: date = Field(alias="from")
    to: date


class Period(DateRange):
    days: int
    complete: bool
    compared_to: DateRange


class StageMetrics(Contract):
    coding: Metric
    pickup: Metric
    review: Metric
    merge: Metric


class Efficiency(Contract):
    merged_prs: Metric
    effective_throughput: Metric
    cycle_time_p50_hours: Metric
    cycle_time_p90_hours: Metric
    stage_p50_hours: StageMetrics
    merged_within_n_days: Metric
    waiting_share: Metric
    waste_share: Metric
    avg_review_rounds: Metric
    post_review_commit_share: Metric
    review_concentration_top_k: Metric
    revert_rate: Metric
    pr_size_p50_lines: Metric
    predictability: dict[str, Any] | None
    survival: dict[str, Any] | None


class StateLedger(Contract):
    pr_hours: float
    previous_pr_hours: float | None
    share: float
    previous_share: float | None
    change_pp: float | None


class TimeLedger(Contract):
    scope: Literal["merged_prs"]
    merged_prs: int
    previous_merged_prs: int | None
    total_pr_hours: float
    previous_total_pr_hours: float | None
    states: dict[State, StateLedger]
    ci_coverage: float
    ci_data_available: bool


class WhatIf(Contract):
    stage: Literal["pickup", "merge", "ci"]
    location: str | None
    target_hours: float
    affected_prs: int
    cycle_p50_before_hours: float
    cycle_p50_after_hours: float
    change_rel: float


class FindingEvidence(Contract):
    label: str
    value: float | int | None
    unit: Unit
    ref: str


class Finding(Contract):
    id: str
    rank: int
    type: Literal[
        "review_capacity",
        "review_queue_growth",
        "review_concentration",
        "merge_blocked",
        "ci_wait",
        "rework_high",
        "waste_high",
        "quality_guardrail",
        "external_contributor_wait",
    ]
    severity: Literal["high", "medium", "low"]
    title: str
    location: str | None
    impact_pr_hours: float
    impact_share: float
    evidence: list[FindingEvidence]
    recommendation: str
    what_if: WhatIf | None


class QueueWeek(Contract):
    week_start: date
    days: float
    inflow: int
    outflow: int
    open_at_week_end: int


class ReviewQueue(Contract):
    weeks: list[QueueWeek]
    weeks_total: int
    weeks_inflow_exceeds_outflow: int
    open_growth_rel: float | None


class Location(Contract):
    location: str
    merged_prs: int
    pickup_p50_hours: float | None
    pickup_ratio_vs_rest: float | None
    waiting_reviewer_pr_hours: float
    previous_waiting_reviewer_pr_hours: float | None
    waiting_reviewer_share: float
    inflow: int
    outflow: int
    at_risk_prs: int
    owners_count: int | None


class MergeBlockers(Contract):
    approved_merged_prs: int
    second_approval_share: float | None
    second_approval_wait_p50_hours: float | None
    post_approval_update_share: float | None
    ci_after_approval_p50_hours: float | None


class Pareto(Contract):
    cause: State
    location: str | None
    pr_hours: float
    share: float


class Reviewer(Contract):
    reviewer: str
    reviews: int
    share: float


class ReviewLoad(Contract):
    reviewers: int
    reviews: int
    distribution: list[Reviewer]


class BottleneckAnalysis(Contract):
    review_queue: ReviewQueue
    locations: list[Location]
    merge_blockers: MergeBlockers
    pareto: list[Pareto]
    what_if: list[WhatIf]
    review_load: ReviewLoad
    ci: dict[str, Any] | None


class AtRiskPr(Contract):
    repo: str
    number: int
    title: str
    url: str
    author: str | None
    state: State
    age_hours: float
    threshold_hours: float
    critical_threshold_hours: float
    severity: Literal["critical", "warning"]
    baseline_source: Literal["90d", "180d", "default"]
    locations: list[str]
    external_contributor: bool
    size_lines: int


class AtRiskSummary(Contract):
    total: int
    critical: int
    by_state: dict[State, int]


class Waste(Contract):
    closed_unmerged: int
    by_class: dict[Literal["superseded", "rejected", "abandoned", "no_review"], int]
    lost_while_waiting: int
    late_rejections: int
    wasted_review_share: float | None
    wasted_pr_hours: float


class PrLink(Contract):
    number: int
    url: str


class RevertChain(Contract):
    original: PrLink
    revert: PrLink
    reland: PrLink | None
    exposure_hours: float
    revert_pr_cycle_hours: float


class Rework(Contract):
    reverts: int
    revert_prs: int
    relanded: int
    revert_chains: list[RevertChain]


class Guardrail(Contract):
    cycle_time_p50_change_rel: float | None
    revert_rate: float | None
    previous_revert_rate: float | None
    revert_rate_change_pp: float | None
    verdict: Literal["ok", "watch", "tradeoff_suspected"]


class Change(Contract):
    current: float
    previous: float
    change: float


class StateAttribution(Change):
    share_of_increase: float
    share_of_decrease: float


class LocationAttribution(Contract):
    location: str
    change: float
    share_of_reviewer_increase: float
    share_of_increase: float


class LargePrAttribution(Change):
    share_of_increase: float


class Attribution(Contract):
    basis: Literal["mean_hours_per_merged_pr"]
    cycle_mean_hours: Change
    total_increase_hours: float
    total_decrease_hours: float
    states: dict[
        Literal["coding", "waiting_reviewer", "waiting_author", "waiting_ci", "waiting_merge"],
        StateAttribution,
    ]
    locations: list[LocationAttribution]
    large_prs: LargePrAttribution


class Trend(Contract):
    bottleneck_shift: str | None
    attribution: Attribution | None


class Signals(Contract):
    large_pr_share: Metric
    merged_without_approval_share: Metric
    fast_large_approval_share: Metric
    external_pickup_ratio: Metric
    at_risk_reviewer_top_location_share: Metric


class Week(Contract):
    week_start: date
    days: float
    merged: int
    cycle_p50_hours: float | None
    pickup_p50_hours: float | None
    pr_size_p50_lines: float | None
    waiting_reviewer_share: float | None
    waiting_ci_share: float | None
    reverts: int


class Series(Contract):
    current: list[Week]
    previous: list[Week]


class PerRepo(Contract):
    repo: str
    merged_prs: int
    cycle_time_p50_hours: float | None
    pickup_p50_hours: float | None
    waiting_share: float | None


class Links(Contract):
    self: str
    narrative: str


class DataFreshness(Contract):
    repo: str
    data_version: int
    covered_since: datetime
    last_synced_at: datetime
    last_sync_status: str


class Sample(Contract):
    merged_prs: int
    closed_unmerged_prs: int
    ready_prs: int
    open_prs_at_as_of: int
    human_reviews: int


class Excluded(Contract):
    bot_prs: int
    backport_prs: int
    never_ready_drafts: int


class Meta(Contract):
    schema_version: Literal["1"]
    analytics_version: str
    thresholds_version: str
    location_dimension: str
    time_basis: Literal["utc_wall_clock"]
    comparison_available: bool
    ci_source: Literal["none", "actions"]
    data_freshness: list[DataFreshness]
    sample: Sample
    excluded: Excluded
    location_sources: dict[Literal["label", "codeowners", "directory", "unclassified"], int]


class Snapshot(Contract):
    snapshot_id: str = Field(pattern=r"^s_[0-9a-f]{16}$")
    repos: list[str]
    period: Period
    as_of: datetime
    headline: str
    efficiency: Efficiency
    time_ledger: TimeLedger
    bottlenecks: list[Finding]
    bottleneck_analysis: BottleneckAnalysis
    drivers: dict[str, Any] | None
    at_risk_prs: list[AtRiskPr]
    at_risk_summary: AtRiskSummary
    waste: Waste
    rework: Rework
    guardrail: Guardrail
    trend: Trend
    signals: Signals
    series: Series
    per_repo: list[PerRepo] | None
    links: Links
    meta: Meta


class PendingJob(Contract):
    id: str
    status: str
    phase: str | None
    url: str


class PendingRepo(Contract):
    repo: str
    covered_since: datetime | None
    required_since: datetime
    open_sweep_done: bool
    last_sync_status: str
    reason: Literal["never_synced", "backfill", "open_sweep", "rederive", "stale"]
    job: PendingJob | None


class Pending(Contract):
    status: Literal["pending"]
    detail: str
    retry_after_seconds: int
    repos: list[PendingRepo]


class SyncJobResponse(Contract):
    id: str
    repo: str
    kind: str
    status: str
    phase: str | None
    stats: dict[str, Any]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    url: str


class RepoStatus(Contract):
    repo: str
    default_branch: str | None
    covered_since: datetime | None
    backfill_target_days: int | None
    backfill_complete: bool
    sync_watermark: datetime | None
    last_synced_at: datetime | None
    last_open_sweep_at: datetime | None
    last_sync_status: str
    last_sync_error: str | None
    data_version: int
    latest_job: SyncJobResponse | None


class RepoList(Contract):
    items: list[RepoStatus]


class RiskDetails(Contract):
    severity: Literal["critical", "warning"]
    threshold_hours: float
    critical_threshold_hours: float
    baseline_source: Literal["90d", "180d", "default"]


class StageHours(Contract):
    coding: float | None
    pickup: float | None
    review: float | None
    merge: float | None


class PrRow(Contract):
    repo: str
    number: int
    title: str
    url: str
    author: str | None
    status: Literal["merged", "closed", "open"]
    created_at: datetime
    ready_at: datetime
    merged_at: datetime | None
    closed_at: datetime | None
    size_lines: int
    size_bucket: str
    locations: list[str]
    external_contributor: bool
    is_revert: bool
    reverted: bool
    close_class: str | None
    review_rounds: int
    human_reviews: int
    cycle_hours: float | None
    stage_hours: StageHours
    ledger_hours: dict[State, float]
    current_state: State | None
    current_state_age_hours: float | None
    at_risk: RiskDetails | None


class PrPage(Contract):
    snapshot_id: str
    as_of: datetime
    status: Literal["merged", "closed", "open"]
    total: int
    items: list[PrRow]
    next_cursor: str | None

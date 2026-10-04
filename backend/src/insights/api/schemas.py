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


class Predictability(Contract):
    within_hist_p85: Metric
    weekly_throughput_cv: Metric


class KM(Contract):
    n: int
    median_hours: float | None


class Survival(Contract):
    current: KM | None
    previous: KM | None


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
    predictability: Predictability | None
    survival: Survival | None


class StateLedger(Contract):
    pr_hours: float
    previous_pr_hours: float | None
    share: float
    previous_share: float | None
    change_pp: float | None


class TimeLedger(Contract):
    merged_prs: int
    total_pr_hours: float
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
    net_inflow_share: float | None


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
    second_approval_share: float | None
    post_approval_update_share: float | None


class Reviewer(Contract):
    reviewer: str
    reviews: int
    share: float


class ReviewLoad(Contract):
    reviewers: int
    reviews: int
    distribution: list[Reviewer]


class CI(Contract):
    queue_p50_minutes: Metric
    run_p50_minutes: Metric
    flaky_rerun_rate: Metric


class BottleneckAnalysis(Contract):
    review_queue: ReviewQueue
    locations: list[Location]
    merge_blockers: MergeBlockers
    review_load: ReviewLoad
    ci: CI | None


class PickupGroup(Contract):
    n: int
    pickup_p50_hours: float | None


class Assignment(Contract):
    assigned: PickupGroup
    unassigned: PickupGroup
    ratio: float | None


class ReviewRoundCost(Contract):
    re_review_wait_p50_hours: float | None


class SlowFeature(Contract):
    feature: Literal[
        "size_lines_p50",
        "external_share",
        "multi_location_share",
        "review_rounds_p50",
        "unrequested_share",
    ]
    slowest: float | None
    rest: float | None
    ratio: float | None


class SlowestDecile(Contract):
    n: int
    features: list[SlowFeature]


class Drivers(Contract):
    assignment: Assignment
    review_round_cost: ReviewRoundCost
    slowest_decile: SlowestDecile | None


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


class AtRiskSummary(Contract):
    total: int
    critical: int


class Waste(Contract):
    lost_while_waiting: int
    late_rejections: int
    wasted_pr_hours: float


class PrLink(Contract):
    number: int
    url: str


class RevertChain(Contract):
    revert: PrLink


class Rework(Contract):
    revert_chains: list[RevertChain]


class Guardrail(Contract):
    cycle_time_p50_change_rel: float | None
    revert_rate: float | None
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
    share_of_increase: float


class LargePrAttribution(Change):
    share_of_increase: float


class Attribution(Contract):
    basis: Literal["mean_hours_per_merged_pr"]
    cycle_mean_hours: Change
    states: dict[
        Literal["coding", "waiting_reviewer", "waiting_author", "waiting_ci", "waiting_merge"],
        StateAttribution,
    ]
    locations: list[LocationAttribution]
    large_prs: LargePrAttribution


class Trend(Contract):
    attribution: Attribution | None


class Signals(Contract):
    large_pr_share: Metric
    merged_without_approval_share: Metric
    fast_large_approval_share: Metric
    external_pickup_ratio: Metric
    at_risk_reviewer_top_location_share: Metric


class Week(Contract):
    week_start: date
    merged: int
    cycle_p50_hours: float | None
    pickup_p50_hours: float | None
    pr_size_p50_lines: float | None
    waiting_reviewer_share: float | None
    waiting_ci_share: float | None


class Series(Contract):
    current: list[Week]
    previous: list[Week]


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
    open_prs_at_as_of: int


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
    drivers: Drivers | None
    at_risk_prs: list[AtRiskPr]
    at_risk_summary: AtRiskSummary
    waste: Waste
    rework: Rework
    guardrail: Guardrail
    trend: Trend
    signals: Signals
    series: Series
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


class DateLimits(Contract):
    earliest_from: date
    latest_to: date
    max_days: int


class RepoList(Contract):
    items: list[RepoStatus]
    date_limits: DateLimits


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


class LlmDowngrade(Contract):
    original: str = Field(alias="from")
    to: str
    reason: str


class ConfidenceBasis(Contract):
    signal_agreement: float | None
    signals_present: int | None
    signals_total: int | None
    effect_size: float | None
    persistence: float | None
    weeks_holding: str | None
    sample_adequacy: float | None
    sample_size: int | None
    localization: float | None
    counter_evidence: int | None
    covers_both_parts: bool | None
    raw_score: float | None
    cap: float | None
    cap_reason: str | None
    llm_downgrade: LlmDowngrade | None


class EvidenceStep(Contract):
    step: Literal["symptom", "stage", "location", "mechanism", "cited"]
    evidence: list[str]


class RuledOut(Contract):
    hypothesis: str
    evidence: list[str]


class OpenAlternative(Contract):
    hypothesis: str
    reason: Literal["no_data", "insufficient_sample", "below_threshold", "not_selected"]


class NarrativeHypothesis(Contract):
    id: str
    source: Literal["library", "llm"]
    title: str
    location: str | None
    statement: str
    confidence: float
    confidence_level: Literal["high", "medium", "low"]
    confidence_basis: ConfidenceBasis
    evidence_chain: list[EvidenceStep]
    counter_evidence: list[str]
    alternatives_ruled_out: list[RuledOut]
    alternatives_open: list[OpenAlternative]
    action: str | None
    verify_next: str | None


class EvidenceEntry(Contract):
    id: str
    key: str
    label: str
    unit: Unit
    value: int | float
    previous: int | float | None
    change_abs: float | None
    change_rel: float | None
    change_pp: float | None
    significant: bool | None
    n: int | None
    side: Literal["efficiency", "bottleneck"]
    baseline: str | None
    location: str | None
    extra: dict[str, int | float | None]
    ref: str
    examples: list[str]


class NarrativeMeta(Contract):
    generated_by: Literal["llm", "template"]
    model: str
    prompt_version: str
    validation: Literal["passed", "failed", "not_run"]
    attempts: int
    fallback_reason: Literal["llm_disabled", "llm_error", "validation_failed", "llm_busy"] | None
    violations: list[str]
    confidence_method: Literal["deterministic-v1"]
    pack_hash: str
    generated_at: datetime


class Narrative(Contract):
    snapshot_id: str
    audience: Literal["director", "manager"]
    lang: Literal["en"]
    narrative: str
    abstained: bool
    abstain_reason: Literal["no_comparison", "no_slowdown", "insufficient_signal"] | None
    hypotheses: list[NarrativeHypothesis]
    evidence: list[EvidenceEntry]
    links: dict[str, str]
    meta: NarrativeMeta

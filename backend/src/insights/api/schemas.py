"""Explicit public snapshot contract; unexpected fields fail validation."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from insights.analytics.snapshot import iso

State = Literal["waiting_reviewer", "waiting_author", "waiting_merge"]
Unit = Literal[
    "hours", "minutes", "count", "share", "ratio", "lines", "rounds", "coefficient", "change"
]


class Contract(BaseModel):
    """Base for response models: rejects unknown fields and NaN/inf, emits UTC Z timestamps."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True)

    @field_serializer("*", when_used="json")
    def serialize_timestamp(self, value: Any) -> Any:
        """Serialize datetime values as whole-second UTC strings; leave other values unchanged."""
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


class Efficiency(Contract):
    merged_prs: Metric
    cycle_time_p50_hours: Metric
    pickup_p50_hours: Metric
    avg_review_rounds: Metric
    post_review_commit_share: Metric
    review_concentration_top_k: Metric
    pr_size_p50_lines: Metric
    large_pr_share: Metric


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


class ReviewQueue(Contract):
    weeks_total: int
    weeks_inflow_exceeds_outflow: int


class Location(Contract):
    location: str
    merged_prs: int
    pickup_p50_hours: float | None
    pickup_ratio_vs_rest: float | None
    waiting_reviewer_pr_hours: float
    previous_waiting_reviewer_pr_hours: float | None


class BottleneckAnalysis(Contract):
    review_queue: ReviewQueue
    locations: list[Location]


class Drivers(Contract):
    slowest_decile_size_ratio: float | None


class Change(Contract):
    current: float
    previous: float
    change: float


class StateAttribution(Change):
    share_of_increase: float


class LocationAttribution(Contract):
    location: str
    change: float
    share_of_increase: float


class Attribution(Contract):
    basis: Literal["mean_hours_per_merged_pr"]
    states: dict[
        Literal["coding", "waiting_reviewer", "waiting_author", "waiting_merge"],
        StateAttribution,
    ]
    locations: list[LocationAttribution]
    large_prs: StateAttribution


class Trend(Contract):
    attribution: Attribution | None


class Week(Contract):
    week_start: date
    merged: int
    cycle_p50_hours: float | None
    pickup_p50_hours: float | None
    pr_size_p50_lines: float | None
    waiting_reviewer_share: float | None


class Series(Contract):
    current: list[Week]
    previous: list[Week]


class Sample(Contract):
    merged_prs: int


class Meta(Contract):
    analytics_version: str
    thresholds_version: str
    location_dimension: str
    comparison_available: bool
    sample: Sample


class Snapshot(Contract):
    snapshot_id: str = Field(pattern=r"^s_[0-9a-f]{16}$")
    repos: list[str]
    period: Period
    as_of: datetime
    efficiency: Efficiency
    time_ledger: TimeLedger
    bottleneck_analysis: BottleneckAnalysis
    drivers: Drivers
    trend: Trend
    series: Series
    meta: Meta


class PendingJob(Contract):
    status: str
    phase: str | None


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


class RepoStatus(Contract):
    repo: str
    last_sync_status: str


class DateLimits(Contract):
    earliest_from: date
    latest_to: date
    max_days: int


class GithubProblem(Contract):
    repo: str
    status: Literal["missing_token", "auth_error", "not_found"]
    syncing: bool


class GithubSetup(Contract):
    token_configured: bool
    problems: list[GithubProblem]


class LlmSetup(Contract):
    key_configured: bool
    region: str
    model_id: str
    last_error: str | None
    last_error_at: datetime | None


class SetupStatus(Contract):
    """Configuration health for the dashboard; reports presence only, never secret values."""

    github: GithubSetup
    llm: LlmSetup


class RepoList(Contract):
    items: list[RepoStatus]
    date_limits: DateLimits
    setup: SetupStatus


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
    reason: Literal["no_data", "insufficient_sample", "below_threshold"]


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
    narrative: str
    abstained: bool
    abstain_reason: Literal["no_comparison", "no_slowdown", "insufficient_signal"] | None
    hypotheses: list[NarrativeHypothesis]
    evidence: list[EvidenceEntry]
    meta: NarrativeMeta

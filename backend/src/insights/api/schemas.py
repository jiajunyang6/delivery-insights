"""Explicit public API contract; unexpected fields fail validation."""

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


class DateRange(Contract):
    start: date = Field(alias="from")
    to: date


class Period(DateRange):
    days: int
    complete: bool
    compared_to: DateRange


class StateLedger(Contract):
    pr_hours: float
    previous_pr_hours: float | None
    share: float
    previous_share: float | None
    change_pp: float | None


class TimeLedger(Contract):
    total_pr_hours: float
    states: dict[State, StateLedger]


class LargestWait(Contract):
    state: State
    share: float
    previous_share: float | None


class LargestChange(Contract):
    state: State
    change_pp: float


class CycleTime(Contract):
    value: float | None
    previous: float | None
    change_rel: float | None
    significant: bool | None
    n: int


class MergedPrs(Contract):
    value: int
    previous: int | None


class InsightSummary(Contract):
    statement: str
    largest_wait: LargestWait | None
    largest_change: LargestChange | None
    cycle_time_p50_hours: CycleTime
    merged_prs: MergedPrs


class InsightLinks(Contract):
    narrative: str


class Insight(Contract):
    """Where PR time goes in one repository and period; the snapshot behind it stays internal."""

    snapshot_id: str = Field(pattern=r"^s_[0-9a-f]{16}$")
    repo: str
    period: Period
    as_of: datetime
    comparison_available: bool
    insight: InsightSummary
    time_ledger: TimeLedger
    links: InsightLinks


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

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
    """Inclusive UTC date range; serialized with a `from` key."""

    start: date = Field(alias="from")
    to: date


class Period(DateRange):
    """The requested period and the equal-length period it is compared with."""

    days: int
    complete: bool
    compared_to: DateRange


class StateLedger(Contract):
    """PR-hours and share for one waiting state, current and previous."""

    pr_hours: float
    previous_pr_hours: float | None
    share: float
    previous_share: float | None
    change_pp: float | None


class TimeLedger(Contract):
    """Post-ready waiting time of merged PRs, split by waiting state."""

    total_pr_hours: float
    states: dict[State, StateLedger]


class LargestWait(Contract):
    """The waiting state with the largest share of PR time."""

    state: State
    share: float
    previous_share: float | None


class LargestChange(Contract):
    """The waiting state whose share moved most, in percentage points."""

    state: State
    change_pp: float


class CycleTime(Contract):
    """Median cycle time with its previous value and significance."""

    value: float | None
    previous: float | None
    change_rel: float | None
    significant: bool | None
    n: int


class MergedPrs(Contract):
    """Merged PR counts for the current and previous periods."""

    value: int
    previous: int | None


class InsightSummary(Contract):
    """The headline: a factual statement plus the numbers it is built from."""

    statement: str
    largest_wait: LargestWait | None
    largest_change: LargestChange | None
    cycle_time_p50_hours: CycleTime
    merged_prs: MergedPrs


class InsightLinks(Contract):
    """Related resources for this insight."""

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
    """Status and phase of the sync job that will make the data ready."""

    status: str
    phase: str | None


class PendingRepo(Contract):
    """Why one repository is not ready yet and how far sync has progressed."""

    repo: str
    covered_since: datetime | None
    required_since: datetime
    open_sweep_done: bool
    last_sync_status: str
    reason: Literal["never_synced", "backfill", "open_sweep", "rederive", "stale"]
    job: PendingJob | None


class Pending(Contract):
    """202 body while the requested period is still syncing."""

    status: Literal["pending"]
    detail: str
    retry_after_seconds: int
    repos: list[PendingRepo]


class RepoStatus(Contract):
    """A tracked repository and the status of its last finished sync.

    `last_sync_error` is the sanitized error code of a failed sync (never upstream text),
    `last_synced_at` the last stored checkpoint, and `syncing` whether a sync is queued or
    running now.
    """

    repo: str
    last_sync_status: str
    last_sync_error: str | None
    last_synced_at: datetime | None
    syncing: bool


class DateLimits(Contract):
    """Dates the API accepts, derived from the backfill horizon."""

    earliest_from: date
    latest_to: date
    max_days: int


class GithubProblem(Contract):
    """A sync status that a `.env` change can fix."""

    repo: str
    status: Literal["missing_token", "auth_error", "not_found"]
    syncing: bool


class GithubSetup(Contract):
    """Whether a GitHub token is set, and repositories it failed for."""

    token_configured: bool
    problems: list[GithubProblem]


class LlmSetup(Contract):
    """Bedrock target and its latest error code, if any."""

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
    """Response of /v1/repos."""

    items: list[RepoStatus]
    date_limits: DateLimits
    setup: SetupStatus


class LlmDowngrade(Contract):
    """A band lowered by the LLM, with its cited reason."""

    original: str = Field(alias="from")
    to: str
    reason: str


class ConfidenceBasis(Contract):
    """The scoring inputs behind a hypothesis's evidence strength."""

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
    """One step of an evidence chain and the evidence IDs supporting it."""

    step: Literal["symptom", "stage", "location", "mechanism", "cited"]
    evidence: list[str]


class RuledOut(Contract):
    """Another hypothesis ruled out, with the evidence that rules it out."""

    hypothesis: str
    evidence: list[str]


class OpenAlternative(Contract):
    """Another hypothesis that could not be assessed, with the reason."""

    hypothesis: str
    reason: Literal["no_data", "insufficient_sample", "below_threshold"]


class NarrativeHypothesis(Contract):
    """One scored hypothesis with its wording, evidence chain and action."""

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
    """One numbered evidence item; `ref` points into the internal snapshot."""

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
    """How the narrative was produced: LLM or template, validation, fallback."""

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
    """Cited narrative for one snapshot."""

    snapshot_id: str
    narrative: str
    abstained: bool
    abstain_reason: Literal["no_comparison", "no_slowdown", "insufficient_signal"] | None
    hypotheses: list[NarrativeHypothesis]
    evidence: list[EvidenceEntry]
    meta: NarrativeMeta

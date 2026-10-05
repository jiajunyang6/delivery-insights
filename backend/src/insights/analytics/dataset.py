"""Immutable inputs shared by database loading and synthetic evaluation."""

from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from insights.analytics import ANALYTICS_VERSION
from insights.analytics.classify import is_flow
from insights.analytics.thresholds import THRESHOLDS_VERSION
from insights.analytics.timeline import Interval, ledger_hours
from insights.analytics.types import PrFacts
from insights.domain import CiRun


@dataclass(frozen=True, slots=True)
class SnapshotParams:
    repos: tuple[str, ...]
    period_from: date
    period_to: date
    location_dimension: str = "label:area-"
    directory_depth: int = 2
    ci_source: str = "none"

    def canonical_dict(self) -> dict[str, Any]:
        """Return normalized parameters and versions used to identify deterministic snapshots."""
        return {
            "repos": sorted(repo.lower() for repo in self.repos),
            "from": self.period_from.isoformat(),
            "to": self.period_to.isoformat(),
            "location_dimension": self.location_dimension,
            "directory_depth": self.directory_depth,
            "ci_source": self.ci_source,
            "analytics_version": ANALYTICS_VERSION,
            "thresholds_version": THRESHOLDS_VERSION,
        }


@dataclass(frozen=True, slots=True)
class RepoData:
    repo: str
    data_version: int
    covered_since: datetime
    last_synced_at: datetime
    last_sync_status: str = "ok"
    repo_id: int | None = None


@dataclass(frozen=True, slots=True)
class PrData:
    pr_id: int
    repo: str
    facts: PrFacts
    intervals: tuple[Interval, ...]
    number: int
    title: str
    url: str
    author: str | None
    is_draft: bool
    created_at: datetime
    human_activity_at: tuple[datetime, ...] = ()

    def __post_init__(self) -> None:
        """Sort activity timestamps on the frozen record for bisect-based cohort selection."""
        object.__setattr__(self, "human_activity_at", tuple(sorted(self.human_activity_at)))


@dataclass(frozen=True, slots=True)
class Review:
    reviewer: str
    occurred_at: datetime
    pr_id: int


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime

    def contains(self, at: datetime | None) -> bool:
        # Half-open [start, end): adjacent periods and weeks never both count a boundary event.
        """Test membership in [start, end); None never belongs to a window."""
        return at is not None and self.start <= at < self.end


@dataclass(frozen=True, slots=True)
class Dataset:
    repos: tuple[RepoData, ...]
    prs: tuple[PrData, ...]
    reviews: tuple[Review, ...]
    period_from: date
    period_to: date
    ci_runs: tuple[tuple[str, CiRun], ...] = ()
    current_day: bool = False
    # init=False gives every dataclasses.replace() an empty, independent cache.
    cohort_cache: dict[Window, tuple[tuple[PrData, ...], tuple[Review, ...] | None]] = field(
        default_factory=dict, init=False, compare=False, repr=False
    )

    @property
    def start(self) -> datetime:
        """Inclusive report start at UTC midnight."""
        return datetime.combine(self.period_from, datetime.min.time(), UTC)

    @property
    def to_excl(self) -> datetime:
        """UTC midnight after the inclusive report end date."""
        return datetime.combine(self.period_to + timedelta(days=1), datetime.min.time(), UTC)

    @property
    def as_of(self) -> datetime:
        # Clamp to the least recently synced repo so no repo is read past its synced data.
        """Observation cutoff: the earlier of report end and the least-recent repository sync."""
        return min(self.to_excl, min(r.last_synced_at for r in self.repos))

    @property
    def days(self) -> int:
        """Number of requested calendar days, including both report dates."""
        return (self.period_to - self.period_from).days + 1

    @property
    def current(self) -> Window:
        """Current half-open window, clipped to the common observation cutoff."""
        return Window(self.start, self.as_of)

    @property
    def previous(self) -> Window:
        """Previous window of equal requested length, ending at the current start."""
        return Window(self.start - timedelta(days=self.days), self.start)

    @property
    def comparison_available(self) -> bool:
        """Whether every repository's history covers the entire previous window."""
        return all(r.covered_since <= self.previous.start for r in self.repos)

    @property
    def flow(self) -> tuple[PrData, ...]:
        """Eligible period-active PRs in the current window."""
        return self.flow_in(self.current)

    def flow_in(self, window: Window) -> tuple[PrData, ...]:
        """Select ready, non-bot, non-backport PRs created or human-active in this window.

        Ended/open status is filtered by each consumer; creation/activity decides membership.
        The cache belongs to this Dataset, so replacing its inputs cannot reuse an old cohort.
        """
        cached = self.cohort_cache.get(window)
        if cached is None:
            flow = tuple(p for p in self.prs if is_flow(p.facts) and active_in(p, window))
            self.cohort_cache[window] = flow, None
            return flow
        return cached[0]

    def reviews_in(self, window: Window) -> tuple[Review, ...]:
        """Review events in the window on its active flow cohort; n counts reviews, not PRs."""
        flow = self.flow_in(window)
        cached = self.cohort_cache[window][1]
        if cached is None:
            ids = {p.pr_id for p in flow}
            cached = tuple(
                r for r in self.reviews if r.pr_id in ids and window.contains(r.occurred_at)
            )
            self.cohort_cache[window] = flow, cached
        return cached


def active_in(pr: PrData, window: Window) -> bool:
    """Period-active rule: created in the window or with recorded human activity in it.

    Bot-driven updates never qualify. The same rule selects the previous-period cohort.
    """
    if window.contains(pr.created_at):
        return True
    index = bisect_left(pr.human_activity_at, window.start)
    return index < len(pr.human_activity_at) and pr.human_activity_at[index] < window.end


def merged(dataset: Dataset, window: Window, *, scope: Window | None = None) -> tuple[PrData, ...]:
    """Flow PRs merged in window, drawn from the active cohort of scope (default: window)."""
    return tuple(p for p in dataset.flow_in(scope or window) if window.contains(p.facts.merged_at))


def hours(pr: PrData, *, end: datetime) -> dict[str, float]:
    """Sum post-ready elapsed waiting hours, clipped to lifecycle end and the supplied cutoff."""
    return ledger_hours(
        pr.intervals, start=pr.facts.ready_at or end, end=min(pr.facts.end_at or end, end)
    )


def effective_review(pr: PrData) -> datetime | None:
    """Return first-review time clamped to ready time, or None if either is missing."""
    f = pr.facts
    return max(f.first_review_at, f.ready_at) if f.first_review_at and f.ready_at else None


def weeks(window: Window) -> Iterator[Window]:
    """Yield Monday-aligned UTC windows, clipping partial weeks to the supplied bounds."""
    start = window.start
    while start < window.end:
        next_monday = datetime.combine(
            start.date() + timedelta(days=7 - start.weekday()), datetime.min.time(), UTC
        )
        end = min(next_monday, window.end)
        yield Window(start, end)
        start = end

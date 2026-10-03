"""Immutable inputs shared by database loading and synthetic evaluation."""

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from insights.analytics import ANALYTICS_VERSION
from insights.analytics.classify import is_flow
from insights.analytics.thresholds import THRESHOLDS_VERSION
from insights.analytics.timeline import Interval, is_open_at, ledger_hours
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
    owners: tuple[tuple[str, int], ...] = ()


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
    ci_intervals: tuple[tuple[datetime, datetime], ...] = ()
    human_activity_at: tuple[datetime, ...] = ()


@dataclass(frozen=True, slots=True)
class Review:
    reviewer: str
    occurred_at: datetime
    pr_id: int


@dataclass(frozen=True, slots=True)
class Baseline:
    repo: str
    state: str
    end_at: datetime
    hours: float


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime

    def contains(self, at: datetime | None) -> bool:
        return at is not None and self.start <= at < self.end


@dataclass(frozen=True, slots=True)
class Dataset:
    repos: tuple[RepoData, ...]
    prs: tuple[PrData, ...]
    reviews: tuple[Review, ...]
    baselines: tuple[Baseline, ...]
    period_from: date
    period_to: date
    ci_runs: tuple[tuple[str, CiRun], ...] = ()
    history: tuple[tuple[str, datetime, float], ...] = ()
    current_day: bool = False
    observation_time: datetime | None = None

    @property
    def start(self) -> datetime:
        return datetime.combine(self.period_from, datetime.min.time(), UTC)

    @property
    def to_excl(self) -> datetime:
        return datetime.combine(self.period_to + timedelta(days=1), datetime.min.time(), UTC)

    @property
    def as_of(self) -> datetime:
        return min(self.to_excl, self.observation_time or min(r.last_synced_at for r in self.repos))

    @property
    def days(self) -> int:
        return (self.period_to - self.period_from).days + 1

    @property
    def current(self) -> Window:
        return Window(self.start, self.as_of)

    @property
    def previous(self) -> Window:
        return Window(self.start - timedelta(days=self.days), self.start)

    @property
    def comparison_available(self) -> bool:
        return all(r.covered_since <= self.previous.start for r in self.repos)

    @property
    def flow(self) -> tuple[PrData, ...]:
        return self.flow_in(self.current)

    def flow_in(self, window: Window) -> tuple[PrData, ...]:
        return tuple(p for p in self.prs if is_flow(p.facts) and active_in(p, window))

    def reviews_in(self, window: Window) -> tuple[Review, ...]:
        ids = {p.pr_id for p in self.flow_in(window)}
        return tuple(r for r in self.reviews if r.pr_id in ids and window.contains(r.occurred_at))

    def for_repo(self, repo: str) -> "Dataset":
        prs = tuple(p for p in self.prs if p.repo == repo)
        ids = {p.pr_id for p in prs}
        return replace(
            self,
            repos=tuple(r for r in self.repos if r.repo == repo),
            observation_time=self.as_of,
            prs=prs,
            reviews=tuple(r for r in self.reviews if r.pr_id in ids),
            baselines=tuple(b for b in self.baselines if b.repo == repo),
            ci_runs=tuple(r for r in self.ci_runs if r[0] == repo),
            history=tuple(h for h in self.history if h[0] == repo),
        )


def active_in(pr: PrData, window: Window) -> bool:
    """Select new PRs or recorded human activity, never bot-driven updated_at."""
    return window.contains(pr.created_at) or any(window.contains(at) for at in pr.human_activity_at)


def merged(dataset: Dataset, window: Window, *, scope: Window | None = None) -> tuple[PrData, ...]:
    return tuple(p for p in dataset.flow_in(scope or window) if window.contains(p.facts.merged_at))


def closed(dataset: Dataset, window: Window) -> tuple[PrData, ...]:
    return tuple(
        p
        for p in dataset.flow_in(window)
        if p.facts.merged_at is None and window.contains(p.facts.closed_at)
    )


def open_at(pr: PrData, at: datetime) -> bool:
    return is_open_at(pr.facts.ready_at, pr.facts.end_at, pr.intervals, at)


def hours(pr: PrData, *, end: datetime) -> dict[str, float]:
    return ledger_hours(
        pr.intervals, start=pr.facts.ready_at or end, end=min(pr.facts.end_at or end, end)
    )


def reverted(pr: PrData, at: datetime) -> bool:
    return pr.facts.reverted_at is not None and pr.facts.reverted_at < at


def effective_review(pr: PrData) -> datetime | None:
    f = pr.facts
    return max(f.first_review_at, f.ready_at) if f.first_review_at and f.ready_at else None


def weeks(window: Window) -> Iterator[Window]:
    start = window.start
    while start < window.end:
        next_monday = datetime.combine(
            start.date() + timedelta(days=7 - start.weekday()), datetime.min.time(), UTC
        )
        end = min(next_monday, window.end)
        yield Window(start, end)
        start = end

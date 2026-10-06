"""Per-PR delivery facts, flow eligibility and locations; derived during sync, no I/O.

PrFacts are persisted and read back as analytics input.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from insights.analytics import DIRECTORY_LOCATIONS_PER_PR
from insights.analytics.timeline import TimelineResult, first_commit_at, human_event
from insights.domain import Event, EventKind, PullRequestRecord


@dataclass(frozen=True, slots=True)
class PrFacts:
    """Per-PR facts derived during sync; durations are hours, None when unknown."""

    number: int = 0
    is_bot_author: bool = False
    is_backport: bool = False
    ready_at: datetime | None = None
    first_review_at: datetime | None = None
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    end_at: datetime | None = None
    coding_hours: float | None = None
    pickup_hours: float | None = None
    cycle_hours: float | None = None
    review_rounds: int = 0
    commits_after_first_review: int = 0
    size_lines: int = 0
    locations: tuple[str, ...] = ()


def is_flow(facts: PrFacts) -> bool:
    """Return whether the PR is ready and eligible for human, non-backport flow metrics."""
    return not facts.is_bot_author and not facts.is_backport and facts.ready_at is not None


def locations_for(pr: PullRequestRecord, dimension: str, depth: int) -> tuple[str, ...]:
    """Return matching labels, else the most-touched directories, else "unclassified".

    Directory locations are the DIRECTORY_LOCATIONS_PER_PR most-touched paths at depth.
    """
    if dimension.startswith("label:"):
        prefix = dimension[6:].lower()
        labels = tuple(sorted({label for label in pr.labels if label.lower().startswith(prefix)}))
        if labels:
            return labels
    if pr.files:
        directories = Counter(
            "dir:" + ("/".join(path.split("/")[:depth]) if "/" in path else "/")
            for path in pr.files
        )
        return tuple(
            name
            for name, _ in sorted(directories.items(), key=lambda item: (-item[1], item[0]))[
                :DIRECTORY_LOCATIONS_PER_PR
            ]
        )
    return ("unclassified",)


def hours_between(start: datetime | None, end: datetime | None) -> float | None:
    """Return signed elapsed hours, or None when either endpoint is absent."""
    return (end - start).total_seconds() / 3600 if start is not None and end is not None else None


def compute_facts(
    pr: PullRequestRecord,
    events: Sequence[Event],
    timeline: TimelineResult,
    *,
    default_branch: str,
    location_dimension: str = "label:area-",
    directory_depth: int = 2,
) -> PrFacts:
    """Derive one PR's facts from its events and timeline.

    Durations are hours and None when an endpoint is missing. The first review counts human,
    non-author reviewers only. Coding and pickup are clamped at 0 since commits can be authored
    after ready_at and reviews can land on drafts. cycle_hours runs from the first commit (or
    creation) to merge.
    """
    first_review = min(
        (
            event.occurred_at
            for event in events
            if event.kind == EventKind.REVIEW and human_event(event, pr.author.login)
        ),
        default=None,
    )
    first_commit = first_commit_at(events)
    coding = hours_between(first_commit, timeline.ready_at)
    pickup = hours_between(timeline.ready_at, first_review)
    closed = pr.closed_at if pr.state == "CLOSED" else None
    return PrFacts(
        number=pr.number,
        is_bot_author=pr.author.is_bot,
        is_backport=bool(default_branch and pr.base_ref != default_branch),
        ready_at=timeline.ready_at,
        first_review_at=first_review,
        merged_at=pr.merged_at,
        closed_at=closed,
        end_at=pr.merged_at or closed,
        coding_hours=max(0.0, coding) if coding is not None else None,
        pickup_hours=max(0.0, pickup) if pickup is not None else None,
        cycle_hours=hours_between(first_commit or pr.created_at, pr.merged_at),
        review_rounds=timeline.review_rounds,
        commits_after_first_review=sum(
            event.kind == EventKind.COMMIT
            and first_review is not None
            and event.occurred_at > first_review
            for event in events
        ),
        size_lines=pr.additions + pr.deletions,
        locations=locations_for(pr, location_dimension, directory_depth),
    )

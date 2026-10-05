"""Per-PR delivery facts computed from one PR's events and timeline; no I/O."""

from collections.abc import Sequence
from datetime import datetime

from insights.analytics.classify import locations_for
from insights.analytics.timeline import TimelineResult, first_commit_at, human_event
from insights.analytics.types import PrFacts
from insights.domain import Event, EventKind, PullRequestRecord


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

from collections.abc import Sequence
from datetime import datetime

from insights.analytics.classify import locations_for
from insights.analytics.thresholds import SIZE_BUCKETS
from insights.analytics.timeline import TimelineResult, first_commit_at, human_event
from insights.analytics.types import PrFacts
from insights.domain import Event, EventKind, OwnershipRule, PullRequestRecord


def hours_between(start: datetime | None, end: datetime | None) -> float | None:
    return (end - start).total_seconds() / 3600 if start is not None and end is not None else None


def count_events(
    events: Sequence[Event],
    kinds: set[EventKind],
    *,
    after: datetime | None,
    before: datetime | None = None,
) -> int:
    return sum(
        e.kind in kinds
        and after is not None
        and e.occurred_at > after
        and (before is None or e.occurred_at < before)
        for e in events
    )


def compute_facts(
    pr: PullRequestRecord,
    events: Sequence[Event],
    timeline: TimelineResult,
    *,
    default_branch: str,
    location_rules: Sequence[OwnershipRule],
    now: datetime,
    location_dimension: str = "label:area-",
    directory_depth: int = 2,
    ci_covered: bool = False,
) -> PrFacts:
    reviews = sorted(
        (
            event
            for event in events
            if event.kind == EventKind.REVIEW and human_event(event, pr.author.login)
        ),
        key=lambda e: e.occurred_at,
    )
    first_review = reviews[0].occurred_at if reviews else None
    approvals = [event for event in reviews if event.payload["state"] == "APPROVED"]
    first_approval = approvals[0].occurred_at if approvals else None
    approvers: dict[str, datetime] = {}
    for event in approvals:
        if event.actor.login:
            approvers.setdefault(event.actor.login.lower(), event.occurred_at)
    approval_times = sorted(approvers.values())
    first_commit = first_commit_at(events)
    response_times = [
        e.occurred_at
        for e in events
        if e.kind == EventKind.COMMENT and human_event(e, pr.author.login)
    ]
    if first_review:
        response_times.append(first_review)
    locations, location_source = locations_for(
        pr, location_dimension, directory_depth, location_rules
    )
    coding = hours_between(first_commit, timeline.ready_at)
    pickup = hours_between(timeline.ready_at, first_review)
    size = pr.additions + pr.deletions
    end = pr.merged_at or (pr.closed_at if pr.state == "CLOSED" else None)
    return PrFacts(
        number=pr.number,
        is_bot_author=pr.author.is_bot,
        is_backport=bool(default_branch and pr.base_ref != default_branch),
        external_contributor=pr.author_association
        in {"CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE"},
        first_commit_at=first_commit,
        ready_at=timeline.ready_at,
        first_response_at=min(response_times, default=None),
        first_review_at=first_review,
        first_approval_at=first_approval,
        approved_at=timeline.approved_at,
        merged_at=pr.merged_at,
        closed_at=pr.closed_at if pr.state == "CLOSED" else None,
        end_at=end,
        coding_hours=max(0.0, coding) if coding is not None else None,
        pickup_hours=max(0.0, pickup) if pickup is not None else None,
        review_hours=hours_between(first_review, timeline.approved_at),
        merge_hours=hours_between(timeline.approved_at, pr.merged_at),
        cycle_hours=hours_between(first_commit or pr.created_at, pr.merged_at),
        review_rounds=timeline.review_rounds,
        feedback_before_approval=sum(
            e.payload["state"] in {"CHANGES_REQUESTED", "COMMENTED"}
            and (first_approval is None or e.occurred_at < first_approval)
            for e in reviews
        ),
        commits_after_first_review=count_events(events, {EventKind.COMMIT}, after=first_review),
        force_pushes_after_first_review=count_events(
            events, {EventKind.FORCE_PUSH}, after=first_review
        ),
        updates_after_approval=count_events(
            events,
            {EventKind.COMMIT, EventKind.FORCE_PUSH},
            after=timeline.approved_at,
            before=end or now,
        ),
        distinct_approvers=len(approvers),
        second_approval_wait_hours=hours_between(approval_times[0], approval_times[1])
        if len(approval_times) > 1
        else None,
        merged_without_approval=bool(pr.merged_at and timeline.approved_at is None),
        review_requested_before_first_review=any(
            e.kind == EventKind.REVIEW_REQUESTED
            and (first_review is None or e.occurred_at < first_review)
            for e in events
        ),
        human_reviews=len(reviews),
        size_lines=size,
        size_bucket=next((label for limit, label in SIZE_BUCKETS if size < limit), "XL"),
        locations=locations,
        location_source=location_source,
        state_at_close=timeline.state_at_end,
        ci_covered=ci_covered,
        ready_weekday=timeline.ready_at.weekday() if timeline.ready_at else None,
        ready_hour=timeline.ready_at.hour if timeline.ready_at else None,
    )

"""Per-PR state timeline (coding, waiting states, closed) derived from events; no I/O."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from insights.domain import Event, EventKind, PullRequestRecord

WAITING_STATES = ("waiting_reviewer", "waiting_author", "waiting_ci", "waiting_merge")
EVENT_ORDER = {
    kind: index
    for index, kind in enumerate(
        (
            EventKind.CLOSED,
            EventKind.REOPENED,
            EventKind.MERGED,
            EventKind.CONVERT_TO_DRAFT,
            EventKind.READY_FOR_REVIEW,
            EventKind.REVIEW_DISMISSED,
            EventKind.REVIEW,
            EventKind.REVIEW_REQUESTED,
            EventKind.REVIEW_REQUEST_REMOVED,
            EventKind.COMMIT,
            EventKind.FORCE_PUSH,
            EventKind.COMMENT,
            EventKind.LABELED,
            EventKind.UNLABELED,
            EventKind.CROSS_REFERENCED,
        )
    )
}


@dataclass(frozen=True, slots=True)
class PrInput:
    author_login: str | None
    created_at: datetime
    is_draft: bool
    merged_at: datetime | None
    closed_at: datetime | None
    state: str


@dataclass(frozen=True, slots=True)
class Interval:
    state: str
    start_at: datetime
    end_at: datetime | None


@dataclass(frozen=True, slots=True)
class TimelineResult:
    ready_at: datetime | None
    intervals: tuple[Interval, ...]
    approved_at: datetime | None
    review_rounds: int
    state_at_end: str | None


def pr_input(pr: PullRequestRecord) -> PrInput:
    return PrInput(
        pr.author.login, pr.created_at, pr.is_draft, pr.merged_at, pr.closed_at, pr.state
    )


def human_event(event: Event, author_login: str | None) -> bool:
    return bool(
        event.actor.login
        and not event.actor.is_bot
        and (author_login is None or event.actor.login.lower() != author_login.lower())
    )


def first_commit_at(events: Sequence[Event]) -> datetime | None:
    return min(
        (
            datetime.fromisoformat(event.payload["authored_at"])
            for event in events
            if event.kind == EventKind.COMMIT
        ),
        default=None,
    )


def compute_ready_at(pr: PrInput, events: Sequence[Event]) -> datetime | None:
    ready = min(
        (e.occurred_at for e in events if e.kind == EventKind.READY_FOR_REVIEW), default=None
    )
    convert = min(
        (e.occurred_at for e in events if e.kind == EventKind.CONVERT_TO_DRAFT), default=None
    )
    if ready is not None and (convert is None or ready < convert):
        return ready
    if pr.is_draft and convert is None:
        return None
    return pr.created_at


def build_timeline(
    pr: PrInput,
    events: Sequence[Event],
    ci_intervals: Sequence[tuple[datetime, datetime]],
    now: datetime,
) -> TimelineResult:
    """Split a PR's life into contiguous state intervals; pure and deterministic.

    Before ready_at there is at most one coding interval. From ready_at the state is re-evaluated
    at every event and CI boundary until merge, close or now; an open PR's last interval has
    end_at None. review_rounds counts entries into waiting_author caused by review feedback.
    state_at_end is set only for PRs closed without merging.
    """
    ready_at = compute_ready_at(pr, events)
    end_at = pr.merged_at or (pr.closed_at if pr.state == "CLOSED" else None)
    horizon = end_at or now
    first_commit = first_commit_at(events)
    coding_start = min(first_commit, ready_at or horizon) if first_commit else pr.created_at
    if ready_at is None:
        intervals = (
            (Interval("coding", coding_start, end_at),)
            if (end_at is None or coding_start < end_at)
            else ()
        )
        return TimelineResult(None, intervals, None, 0, None)
    output = [Interval("coding", coding_start, ready_at)] if coding_start < ready_at else []
    ordered = sorted(events, key=lambda e: (e.occurred_at, EVENT_ORDER[e.kind], e.dedup_key))
    # Only a close that is later reopened pauses the PR; the terminal close ends the timeline.
    paired_closes: set[str] = set()
    next_reopen: datetime | None = None
    for event in reversed(ordered):
        if event.occurred_at >= horizon:
            continue
        if event.kind == EventKind.REOPENED:
            next_reopen = event.occurred_at
        elif event.kind == EventKind.CLOSED and next_reopen and next_reopen >= event.occurred_at:
            paired_closes.add(event.dedup_key)
    decisions: dict[str, tuple[str, str | None]] = {}
    last_feedback: datetime | None = None
    last_update: datetime | None = None
    flags = {"draft": False, "paused": False}

    def review(event: Event) -> bool:
        nonlocal last_feedback
        person = event.actor.login.lower() if event.actor.login else None
        if not person or not human_event(event, pr.author_login):
            return False
        state = event.payload["state"]
        if state in {"APPROVED", "CHANGES_REQUESTED"}:
            decisions[person] = (state, event.payload.get("review_id"))
        if state in {"CHANGES_REQUESTED", "COMMENTED"}:
            last_feedback = event.occurred_at
            return True
        return False

    def dismiss(event: Event) -> bool:
        review_id = event.payload.get("review_id")
        reviewer = (event.payload.get("review_author") or "").lower()
        for login, (_, recorded_id) in list(decisions.items()):
            if (review_id is not None and recorded_id == review_id) or (
                review_id is None and reviewer == login
            ):
                del decisions[login]
        return False

    def toggle(event: Event) -> bool:
        flag, value = {
            EventKind.CONVERT_TO_DRAFT: ("draft", True),
            EventKind.READY_FOR_REVIEW: ("draft", False),
            EventKind.CLOSED: ("paused", True),
            EventKind.REOPENED: ("paused", False),
        }[event.kind]
        if event.kind != EventKind.CLOSED or event.dedup_key in paired_closes:
            flags[flag] = value
        return False

    handlers = {
        EventKind.REVIEW: review,
        EventKind.REVIEW_DISMISSED: dismiss,
        EventKind.CONVERT_TO_DRAFT: toggle,
        EventKind.READY_FOR_REVIEW: toggle,
        EventKind.CLOSED: toggle,
        EventKind.REOPENED: toggle,
    }

    def apply(event: Event) -> bool:
        nonlocal last_update
        handler = handlers.get(event.kind)
        feedback = handler(event) if handler else False
        person = event.actor.login.lower() if event.actor.login else None
        own = bool(person and pr.author_login and person == pr.author_login.lower())
        if event.kind in {EventKind.COMMIT, EventKind.FORCE_PUSH} or (
            own and event.kind in {EventKind.COMMENT, EventKind.REVIEW, EventKind.REVIEW_REQUESTED}
        ):
            last_update = event.occurred_at
        return feedback

    # Precedence: paused, draft, approved with no outstanding change request, unanswered
    # feedback, CI running, else waiting on a reviewer.
    def evaluate(at: datetime) -> str:
        if flags["paused"]:
            return "closed"
        if flags["draft"]:
            return "waiting_author"
        states = {decision[0] for decision in decisions.values()}
        if "APPROVED" in states and "CHANGES_REQUESTED" not in states:
            return "waiting_merge"
        if last_feedback is not None and (last_update is None or last_feedback > last_update):
            return "waiting_author"
        if any(start <= at < end for start, end in ci_intervals):
            return "waiting_ci"
        return "waiting_reviewer"

    grouped: dict[datetime, list[Event]] = defaultdict(list)
    for event in ordered:
        if event.occurred_at <= ready_at:
            apply(event)
        elif event.occurred_at < horizon:
            grouped[event.occurred_at].append(event)
    flags["draft"] = False
    boundaries = set(grouped)
    boundaries.update(
        point for pair in ci_intervals for point in pair if ready_at < point < horizon
    )
    current, segment_start, rounds = evaluate(ready_at), ready_at, 0
    for at in sorted(boundaries):
        feedback = False
        for event in grouped[at]:
            feedback = apply(event) or feedback
        new = evaluate(at)
        if new == "waiting_author" and current != new and not flags["draft"] and feedback:
            rounds += 1
        if new != current:
            if segment_start < at:
                output.append(Interval(current, segment_start, at))
            current, segment_start = new, at
    if end_at is None or segment_start < end_at:
        output.append(Interval(current, segment_start, end_at))
    approved = next((i.start_at for i in output if i.state == "waiting_merge"), None)
    return TimelineResult(
        ready_at,
        tuple(output),
        approved,
        rounds,
        current if pr.state == "CLOSED" and pr.merged_at is None else None,
    )


def state_at(intervals: Sequence[Interval], at: datetime) -> Interval | None:
    return next(
        (
            interval
            for interval in reversed(intervals)
            if interval.start_at <= at and (interval.end_at is None or at < interval.end_at)
        ),
        None,
    )


def is_open_at(
    ready_at: datetime | None,
    end_at: datetime | None,
    intervals: Sequence[Interval],
    at: datetime,
) -> bool:
    interval = state_at(intervals, at)
    return bool(
        ready_at is not None
        and ready_at <= at
        and (end_at is None or end_at > at)
        and interval
        and interval.state in WAITING_STATES
    )


def ledger_hours(
    intervals: Sequence[Interval], *, start: datetime, end: datetime
) -> dict[str, float]:
    result = dict.fromkeys(WAITING_STATES, 0.0)
    for interval in intervals:
        if interval.state in result:
            hours = (
                min(interval.end_at or end, end) - max(interval.start_at, start)
            ).total_seconds() / 3600
            result[interval.state] += max(0.0, hours)
    return result


def check_invariants(result: TimelineResult, pr: PrInput) -> list[str]:
    errors: list[str] = []
    end = pr.merged_at or (pr.closed_at if pr.state == "CLOSED" else None)
    for index, interval in enumerate(result.intervals):
        if interval.end_at is not None and interval.start_at >= interval.end_at:
            errors.append("non_positive_interval")
        if index and result.intervals[index - 1].end_at != interval.start_at:
            errors.append("non_contiguous_intervals")
        if interval.end_at is None and (index < len(result.intervals) - 1 or end is not None):
            errors.append("invalid_open_interval")
        if (
            interval.state == "coding"
            and result.ready_at
            and (interval.end_at is None or interval.end_at > result.ready_at)
        ):
            errors.append("coding_after_ready")
    if result.intervals:
        if result.intervals[-1].state == "closed":
            errors.append("closed_final_interval")
        if result.intervals[-1].end_at != end:
            errors.append("wrong_end")
    if result.ready_at and end and result.ready_at < end:
        after = [i for i in result.intervals if i.state != "coding"]
        if not after or after[0].start_at != result.ready_at:
            errors.append("missing_ready_coverage")
        if (
            abs(
                sum((i.end_at - i.start_at).total_seconds() for i in after if i.end_at)
                - (end - result.ready_at).total_seconds()
            )
            > 1e-6
        ):
            errors.append("wrong_total_duration")
    return errors

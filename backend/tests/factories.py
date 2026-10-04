from dataclasses import replace
from datetime import UTC, datetime, timedelta

from insights.domain import Actor, Event, EventKind, PullRequestRecord

ORIGIN = datetime(2026, 1, 1, tzinfo=UTC)


def at(hours):
    return ORIGIN + timedelta(hours=hours)


def event(kind, hour, actor="reviewer", *, state=None, review_id=None, **payload):
    kind = EventKind(kind)
    if kind == EventKind.REVIEW:
        payload.update(
            state=state or "APPROVED", review_id=review_id or f"r{hour}-{actor}", dismissed=False
        )
    if kind == EventKind.REVIEW_DISMISSED:
        payload["review_id"] = review_id
    if kind == EventKind.COMMIT:
        payload = {
            "oid": "a" * 40,
            "authored_at": at(hour).isoformat(),
            "committed_at": at(hour).isoformat(),
            "reverts": [],
            **payload,
        }
    return Event(
        kind,
        at(hour),
        Actor(actor, bool(actor and actor.endswith("[bot]"))),
        payload,
        f"{kind}-{hour}-{actor}-{review_id}-{payload}",
    )


def record(**changes):
    base = PullRequestRecord(
        number=1,
        title="Change",
        body_excerpt="",
        url="https://github.com/a/b/pull/1",
        state="MERGED",
        is_draft=False,
        author=Actor("author", False),
        author_association="MEMBER",
        base_ref="main",
        head_ref="feature",
        created_at=at(0),
        updated_at=at(10),
        closed_at=at(10),
        merged_at=at(10),
        merge_commit_oid=None,
        additions=20,
        deletions=5,
        labels=("area-A",),
        files=("src/a.py",),
        events=(),
    )
    return replace(base, **changes)

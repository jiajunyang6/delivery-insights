"""Batch conversion of persisted source data into immutable domain records."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import fields

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from insights.analytics.facts import PrFacts
from insights.db.models import PrEvent, PrFact, PrFile, PullRequest
from insights.domain import Actor, Event, EventKind, PullRequestRecord


def facts_from_row(row: PrFact) -> PrFacts:
    """Convert stored fact fields to immutable analytics records with tuple locations."""
    values = {field.name: getattr(row, field.name) for field in fields(PrFacts)}
    values["locations"] = tuple(values["locations"])
    return PrFacts(**values)


async def load_records(
    session: AsyncSession, prs: Sequence[PullRequest]
) -> dict[int, PullRequestRecord]:
    """Load ordered events/files and reconstruct immutable source records keyed by database ID.

    An empty input returns an empty mapping; this function does not commit or derive new facts.
    """
    ids = [pr.id for pr in prs]
    if not ids:
        return {}
    events: dict[int, list[Event]] = defaultdict(list)
    files: dict[int, list[str]] = defaultdict(list)
    for row in await session.scalars(
        select(PrEvent)
        .where(PrEvent.pr_id.in_(ids))
        .order_by(PrEvent.occurred_at, PrEvent.kind, PrEvent.dedup_key)
    ):
        events[row.pr_id].append(
            Event(
                EventKind(row.kind),
                row.occurred_at,
                Actor(row.actor_login, row.actor_is_bot),
                row.payload,
                row.dedup_key,
            )
        )
    for pr_id, path in (
        await session.execute(
            select(PrFile.pr_id, PrFile.path).where(PrFile.pr_id.in_(ids)).order_by(PrFile.path)
        )
    ).all():
        files[pr_id].append(path)
    names = {field.name for field in fields(PullRequestRecord)} - {
        "events",
        "files",
        "author",
        "labels",
    }
    return {
        pr.id: PullRequestRecord(
            **{name: getattr(pr, name) for name in names},
            author=Actor(pr.author_login, pr.is_bot_author),
            labels=tuple(pr.labels),
            events=tuple(events[pr.id]),
            files=tuple(files[pr.id]),
        )
        for pr in prs
    }

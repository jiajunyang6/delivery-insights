from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from insights.config import Settings
from insights.db.models import PrEvent, PrFile, PullRequest, Repository
from insights.domain import PageResult, PullRequestRecord
from insights.sources.github.normalize import content_hash
from insights.sync.derive import derive_prs


@dataclass(frozen=True, slots=True)
class SaveResult:
    prs_changed: int
    events: int
    pr_ids: tuple[int, ...]
    invariant_violations: int = 0


def pr_values(record: PullRequestRecord, repo_id: int, now: datetime) -> dict[str, Any]:
    fields = {
        name: getattr(record, name)
        for name in (
            "source_id",
            "number",
            "title",
            "body_excerpt",
            "url",
            "state",
            "is_draft",
            "author_type",
            "author_association",
            "base_ref",
            "head_ref",
            "created_at",
            "updated_at",
            "closed_at",
            "merged_at",
            "merged_by",
            "merge_commit_oid",
            "additions",
            "deletions",
            "changed_files",
            "files_truncated",
        )
    }
    return {
        **fields,
        "repo_id": repo_id,
        "author_login": record.author.login,
        "is_bot_author": record.author.is_bot,
        "labels": list(record.labels),
        "content_hash": content_hash(record),
        "synced_at": now,
    }


async def save_page(
    session: AsyncSession,
    repo_id: int,
    page: PageResult,
    *,
    now: datetime,
    settings: Settings | None = None,
) -> SaveResult:
    existing = dict(
        (
            await session.execute(
                select(PullRequest.number, PullRequest.content_hash).where(
                    PullRequest.repo_id == repo_id,
                    PullRequest.number.in_([p.number for p in page.prs]),
                )
            )
        ).all()
    )
    records = [p for p in page.prs if existing.get(p.number) != content_hash(p)]
    await session.execute(
        update(Repository)
        .where(Repository.id == repo_id)
        .values(default_branch=page.repository.default_branch, updated_at=now)
    )
    if not records:
        return SaveResult(0, 0, ())
    values = [pr_values(pr, repo_id, now) for pr in records]
    statement = insert(PullRequest).values(values)
    upsert = statement.on_conflict_do_update(
        index_elements=["repo_id", "number"],
        set_={
            field: getattr(statement.excluded, field)
            for field in values[0]
            if field not in {"repo_id", "number"}
        },
    ).returning(PullRequest.number, PullRequest.id)
    ids: dict[int, int] = dict((await session.execute(upsert)).all())
    pr_ids = tuple(ids[pr.number] for pr in records)
    await session.execute(delete(PrEvent).where(PrEvent.pr_id.in_(pr_ids)))
    await session.execute(delete(PrFile).where(PrFile.pr_id.in_(pr_ids)))
    events = [
        {
            "pr_id": ids[pr.number],
            "kind": event.kind.value,
            "occurred_at": event.occurred_at,
            "actor_login": event.actor.login,
            "actor_is_bot": event.actor.is_bot,
            "payload": event.payload,
            "dedup_key": event.dedup_key,
        }
        for pr in records
        for event in pr.events
    ]
    files = [{"pr_id": ids[pr.number], "path": path} for pr in records for path in pr.files]
    if events:
        await session.execute(insert(PrEvent), events)
    if files:
        await session.execute(insert(PrFile), files)
    await session.execute(
        update(Repository)
        .where(Repository.id == repo_id)
        .values(data_version=Repository.data_version + 1)
    )
    violations = await derive_prs(session, pr_ids, settings=settings or Settings(), now=now)
    return SaveResult(len(records), len(events), pr_ids, violations)

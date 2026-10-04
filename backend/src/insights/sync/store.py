from dataclasses import asdict, dataclass, replace
from datetime import datetime
from hashlib import sha256
from typing import Any

import orjson
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from insights.config import Settings
from insights.db.models import PrEvent, PrFile, PullRequest, Repository
from insights.domain import PageResult, PullRequestRecord
from insights.sync.derive import derive_prs


@dataclass(frozen=True, slots=True)
class SaveResult:
    prs_changed: int
    events: int
    pr_ids: tuple[int, ...]
    invariant_violations: int = 0
    prs_created: int = 0


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
    settings: Settings,
) -> SaveResult:
    existing = {
        number: (stored_hash, updated_at)
        for number, stored_hash, updated_at in (
            await session.execute(
                select(PullRequest.number, PullRequest.content_hash, PullRequest.updated_at).where(
                    PullRequest.repo_id == repo_id,
                    PullRequest.number.in_([p.number for p in page.prs]),
                )
            )
        ).all()
    }
    # A backfill page can have been downloaded before a checkpoint's catch-up.
    records = [
        p
        for p in page.prs
        if p.number not in existing
        or (p.updated_at >= existing[p.number][1] and content_hash(p) != existing[p.number][0])
    ]
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
        where=statement.excluded.updated_at >= PullRequest.updated_at,
    ).returning(PullRequest.number, PullRequest.id)
    ids: dict[int, int] = dict((await session.execute(upsert)).all())
    records = [pr for pr in records if pr.number in ids]
    if not records:
        return SaveResult(0, 0, ())
    created = sum(p.number not in existing for p in records)
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
        .values(
            data_version=Repository.data_version + 1,
            # Lifecycle, title/reference, and timeline changes can alter existing links too.
            links_pending=True,
        )
    )
    violations = await derive_prs(session, pr_ids, settings=settings, now=now)
    return SaveResult(len(records), len(events), pr_ids, violations, created)


def content_hash(pr: PullRequestRecord) -> str:
    normalized = replace(
        pr, events=tuple(sorted(pr.events, key=lambda e: (e.occurred_at, e.kind, e.dedup_key)))
    )
    return sha256(orjson.dumps(asdict(normalized), option=orjson.OPT_SORT_KEYS)).hexdigest()

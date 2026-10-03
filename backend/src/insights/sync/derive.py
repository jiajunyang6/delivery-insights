"""Transactional derivation. Pure computation lives in analytics."""

from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Table, bindparam, delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from insights.analytics import derive_key
from insights.analytics.ci import union_intervals
from insights.analytics.classify import LINK_FIELDS, LinkInput, link_prs
from insights.analytics.facts import compute_facts
from insights.analytics.timeline import build_timeline, check_invariants, pr_input
from insights.config import Settings
from insights.db.ci import load_ci_data
from insights.db.models import OwnershipRule as StoredRule
from insights.db.models import PrFact, PrInterval, PullRequest, Repository
from insights.db.records import facts_from_row, load_records
from insights.domain import OwnershipRule


def current_key(settings: Settings) -> str:
    return derive_key(settings.location_dimension, settings.directory_depth)


def pending_prs(repo_id: int, key: str) -> Select[int]:
    return (
        select(PullRequest.id)
        .outerjoin(PrFact, PrFact.pr_id == PullRequest.id)
        .where(PullRequest.repo_id == repo_id, PrFact.derive_key.is_distinct_from(key))
    )


async def derivation_complete(session: AsyncSession, repo_id: int, key: str) -> bool:
    return await session.scalar(pending_prs(repo_id, key).limit(1)) is None


async def derive_prs(
    session: AsyncSession, pr_ids: Sequence[int], *, settings: Settings, now: datetime
) -> None:
    if not pr_ids:
        return
    prs = (await session.scalars(select(PullRequest).where(PullRequest.id.in_(pr_ids)))).all()
    repositories = {
        r.id: r
        for r in await session.scalars(
            select(Repository).where(Repository.id.in_({p.repo_id for p in prs}))
        )
    }
    records = await load_records(session, prs)
    ci = await load_ci_data(session, list(repositories), pr_ids=list(pr_ids))
    rules: dict[int, list[OwnershipRule]] = {identifier: [] for identifier in repositories}
    for row in await session.scalars(
        select(StoredRule).where(StoredRule.repo_id.in_(repositories))
    ):
        rules[row.repo_id].append(
            OwnershipRule(row.source, row.pattern, tuple(row.owners), row.line_no)
        )
    fact_values: list[dict[str, Any]] = []
    interval_values: list[dict[str, Any]] = []
    for pr in prs:
        record = records[pr.id]
        ci_intervals = union_intervals(ci.by_pr.get(pr.id, ()))
        result = build_timeline(pr_input(record), record.events, ci_intervals, now)
        errors = check_invariants(result, pr_input(record))
        if errors:
            raise ValueError(f"Timeline invariant violation for PR {pr.id}: {','.join(errors)}")
        facts = compute_facts(
            record,
            record.events,
            result,
            default_branch=repositories[pr.repo_id].default_branch or "",
            location_rules=rules[pr.repo_id],
            ci_covered=bool(ci_intervals),
            now=now,
            location_dimension=settings.location_dimension,
            directory_depth=settings.directory_depth,
        )
        fact_values.append(
            {
                **asdict(facts),
                "pr_id": pr.id,
                "repo_id": pr.repo_id,
                "derive_key": current_key(settings),
                "computed_at": now,
            }
        )
        interval_values.extend(
            {"pr_id": pr.id, "repo_id": pr.repo_id, "seq": seq, **asdict(interval)}
            for seq, interval in enumerate(result.intervals)
        )
    await session.execute(delete(PrInterval).where(PrInterval.pr_id.in_(pr_ids)))
    if interval_values:
        await session.execute(insert(PrInterval), interval_values)
    if fact_values:
        statement = insert(PrFact).values(fact_values)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=["pr_id"],
                set_={
                    name: getattr(statement.excluded, name)
                    for name in fact_values[0]
                    if name not in {"pr_id", *LINK_FIELDS}
                },
            )
        )


async def link_repo(session: AsyncSession, repo_id: int, *, increment_version: bool = True) -> int:
    repo = await session.get(Repository, repo_id)
    if repo is None:
        raise ValueError("Repository is missing")
    prs = (await session.scalars(select(PullRequest).where(PullRequest.repo_id == repo_id))).all()
    records = await load_records(session, prs)
    stored = {
        row.pr_id: facts_from_row(row)
        for row in await session.scalars(select(PrFact).where(PrFact.repo_id == repo_id))
    }
    linked = link_prs(
        [LinkInput(pr.id, records[pr.id], stored[pr.id]) for pr in prs if pr.id in stored],
        repo_full_name=repo.full_name,
        default_branch=repo.default_branch or "",
    )
    changed = [
        {"target_id": identifier, **{name: getattr(facts, name) for name in LINK_FIELDS}}
        for identifier, facts in linked.items()
        if any(getattr(facts, name) != getattr(stored[identifier], name) for name in LINK_FIELDS)
    ]
    if changed:
        table = cast(Table, PrFact.__table__)
        await session.execute(
            update(table)
            .where(table.c.pr_id == bindparam("target_id"))
            .values({name: bindparam(name) for name in LINK_FIELDS}),
            changed,
        )
        if increment_version:
            await session.execute(
                update(Repository)
                .where(Repository.id == repo_id)
                .values(data_version=Repository.data_version + 1)
            )
    return len(changed)

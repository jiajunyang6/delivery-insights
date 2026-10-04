"""Transactional derivation of timelines, facts and revert/reland links.

Pure computation lives in analytics; this module loads its inputs and writes the results.
"""

from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any, cast

import structlog
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
from insights.sync.queue import (
    enqueue_precompute,
    enqueue_sync,
    now_for,
    run_job,
    sessions_for,
)

logger = structlog.get_logger(__name__)


def current_key(settings: Settings) -> str:
    """Compute the active derivation identity from location grouping and directory depth."""
    return derive_key(settings.location_dimension, settings.directory_depth)


def pending_prs(repo_id: int, key: str) -> Select[int]:
    """Find missing or outdated facts; IS DISTINCT FROM also treats a NULL derive key as stale."""
    return (
        select(PullRequest.id)
        .outerjoin(PrFact, PrFact.pr_id == PullRequest.id)
        .where(PullRequest.repo_id == repo_id, PrFact.derive_key.is_distinct_from(key))
    )


async def derivation_complete(session: AsyncSession, repo_id: int, key: str) -> bool:
    """Test whether all repository PRs have facts matching this derivation key."""
    return await session.scalar(pending_prs(repo_id, key).limit(1)) is None


async def derive_prs(
    session: AsyncSession, pr_ids: Sequence[int], *, settings: Settings, now: datetime
) -> int:
    """Rebuild intervals and facts for the given PRs; return how many violate invariants.

    Stored link fields are kept: they depend on other PRs and are owned by link_repo.
    The caller owns commit/rollback. Invariant violations are logged and counted, not rejected;
    intervals and facts are still rebuilt so the job can report all affected PRs.
    """
    if not pr_ids:
        return 0
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
    invariant_violations = 0
    for pr in prs:
        record = records[pr.id]
        ci_intervals = union_intervals(ci.by_pr.get(pr.id, ()))
        result = build_timeline(pr_input(record), record.events, ci_intervals, now)
        errors = check_invariants(result, pr_input(record))
        if errors:
            invariant_violations += 1
            logger.warning(
                "timeline_invariant_violation",
                repo=repositories[pr.repo_id].full_name,
                number=pr.number,
                codes=errors,
            )
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
            }
        )
        interval_values.extend(
            {"pr_id": pr.id, "repo_id": pr.repo_id, "seq": seq, **asdict(interval)}
            for seq, interval in enumerate(result.intervals)
        )
    await session.execute(delete(PrInterval).where(PrInterval.pr_id.in_(pr_ids)))
    # Replacement is atomic only inside the caller's transaction: readers must not see the
    # deletion separately from the new intervals and facts.
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
    return invariant_violations


async def link_repo(session: AsyncSession, repo_id: int, *, increment_version: bool = True) -> int:
    """Recompute revert/reland links across the repository and clear `links_pending`.

    Writes only changed link fields and returns their PR count. `data_version` is bumped on
    change unless the caller bumps it itself.
    """
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
            # Link changes affect historical waste/throughput even when raw PR rows are unchanged.
            await session.execute(
                update(Repository)
                .where(Repository.id == repo_id)
                .values(data_version=Repository.data_version + 1)
            )
    await session.execute(
        update(Repository).where(Repository.id == repo_id).values(links_pending=False)
    )
    return len(changed)


async def enqueue_rederivation(ctx: dict[str, Any]) -> None:
    """Queue covered tracked repositories whose completed derivation identity is stale."""
    key = current_key(cast(Settings, ctx["settings"]))
    async with sessions_for(ctx)() as session:
        repos = (
            await session.scalars(
                select(Repository).where(
                    Repository.tracked,
                    Repository.covered_since.is_not(None),
                    Repository.derived_key.is_distinct_from(key),
                )
            )
        ).all()
        for repo in repos:
            await enqueue_sync(ctx["redis"], session, repo.full_name, "rederive", now=now_for(ctx))


async def rederive_repo(ctx: dict[str, Any], repo_full_name: str, kind: str, job_id: str) -> str:
    """arq entry point: rederive PRs whose facts lack the current derive key.

    Commits keyset batches of 500, then relinks and sets `derived_key` only after verifying
    no PR is still pending.
    """
    settings = cast(Settings, ctx["settings"])
    key = current_key(settings)
    async with run_job(ctx, repo_full_name, kind, job_id) as job:
        if job is None:
            return "skipped_locked"
        processed = violations = 0
        job.stats = {"prs_derived": 0, "invariant_violations": 0}
        async with sessions_for(ctx)() as session:
            repo = (
                await session.scalars(
                    select(Repository).where(Repository.full_name_lower == repo_full_name.lower())
                )
            ).one()
            complete = repo.derived_key == key and await derivation_complete(session, repo.id, key)
        if not complete:
            # Invalidate readiness even when a corrupted row had a current repository key.
            async with sessions_for(ctx)() as session, session.begin():
                await session.execute(
                    update(Repository)
                    .where(Repository.id == repo.id)
                    .values(derived_key=None if repo.derived_key == key else repo.derived_key)
                )
            after = 0
            while True:
                async with sessions_for(ctx)() as session, session.begin():
                    ids = (
                        await session.scalars(
                            pending_prs(repo.id, key)
                            .where(PullRequest.id > after)
                            .order_by(PullRequest.id)
                            .limit(500)
                        )
                    ).all()
                    if not ids:
                        break
                    violations += await derive_prs(
                        session, ids, settings=settings, now=now_for(ctx)
                    )
                    after = ids[-1]
                    processed += len(ids)
                    job.stats.update(prs_derived=processed, invariant_violations=violations)
            async with sessions_for(ctx)() as session, session.begin():
                await link_repo(session, repo.id, increment_version=False)
                if not await derivation_complete(session, repo.id, key):
                    raise RuntimeError("Repository derivation is incomplete")
                await session.execute(
                    update(Repository)
                    .where(Repository.id == repo.id)
                    .values(derived_key=key, data_version=Repository.data_version + 1)
                )
        job.stats.update(prs_derived=processed, invariant_violations=violations)
        await job.finish()
        if not complete:
            await enqueue_precompute(ctx, repo)
    return job.result

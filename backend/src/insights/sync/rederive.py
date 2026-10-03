from typing import Any, cast

import structlog
from sqlalchemy import select, update

from insights.config import Settings
from insights.db.models import PullRequest, Repository
from insights.sync.derive import (
    current_key,
    derivation_complete,
    derive_prs,
    link_repo,
    pending_prs,
)
from insights.sync.jobs import now_for, repository_lock, sessions_for, set_job
from insights.sync.queue import enqueue_sync

logger = structlog.get_logger(__name__)


async def enqueue_rederivation(ctx: dict[str, Any]) -> None:
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
    settings = cast(Settings, ctx["settings"])
    key = current_key(settings)
    async with repository_lock(ctx, repo_full_name, job_id) as acquired:
        if not acquired:
            return "skipped_locked"
        await set_job(ctx, job_id, status="running", started_at=now_for(ctx), phase=kind)
        processed = 0
        violations = 0
        try:
            async with sessions_for(ctx)() as session:
                repo = (
                    await session.scalars(
                        select(Repository).where(
                            Repository.full_name_lower == repo_full_name.lower()
                        )
                    )
                ).one()
                complete = repo.derived_key == key and await derivation_complete(
                    session, repo.id, key
                )
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
                async with sessions_for(ctx)() as session, session.begin():
                    await link_repo(session, repo.id, increment_version=False)
                    if not await derivation_complete(session, repo.id, key):
                        raise RuntimeError("Repository derivation is incomplete")
                    await session.execute(
                        update(Repository)
                        .where(Repository.id == repo.id)
                        .values(derived_key=key, data_version=Repository.data_version + 1)
                    )
        except Exception as exc:
            await set_job(
                ctx,
                job_id,
                status="failed",
                finished_at=now_for(ctx),
                error=type(exc).__name__,
                stats={"prs_derived": processed, "invariant_violations": violations},
            )
            logger.error(
                "rederive_failed", repo=repo_full_name, job=job_id, error=type(exc).__name__
            )
            return "failed"
        await set_job(
            ctx,
            job_id,
            status="succeeded",
            finished_at=now_for(ctx),
            stats={"prs_derived": processed, "invariant_violations": violations},
        )
        if not complete:
            await ctx["redis"].enqueue_job(
                "precompute_snapshots",
                repo_full_name,
                _job_id=f"precompute:{repo_full_name.lower()}",
            )
        return "succeeded"

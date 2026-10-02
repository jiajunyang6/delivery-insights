from datetime import datetime
from uuid import uuid4

from arq.connections import ArqRedis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from insights.config import REPO_RE
from insights.db.models import Repository, SyncJob

JOB_ROUTES = {
    "backfill": ("sync_repo", "sync"),
    "incremental": ("sync_repo", "sync"),
    "manual": ("sync_repo", "sync"),
    "rederive": ("rederive_repo", "rederive"),
    "ci_runs": ("sync_ci_runs", "ci"),
    "ownership": ("sync_ownership", "owners"),
}


async def ensure_repo(session: AsyncSession, repo_full_name: str, now: datetime) -> Repository:
    match = REPO_RE.fullmatch(repo_full_name)
    if not match:
        raise ValueError("Invalid repository")
    await session.execute(
        insert(Repository)
        .values(
            full_name=repo_full_name,
            full_name_lower=repo_full_name.lower(),
            owner=match["owner"],
            name=match["name"],
            tracked=True,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["full_name_lower"])
    )
    return (
        await session.scalars(
            select(Repository).where(Repository.full_name_lower == repo_full_name.lower())
        )
    ).one()


async def enqueue_sync(
    arq: ArqRedis, session: AsyncSession, repo_full_name: str, kind: str, *, now: datetime
) -> tuple[SyncJob, bool]:
    function, prefix = JOB_ROUTES[kind]
    repo = await ensure_repo(session, repo_full_name, now)
    job = SyncJob(id=uuid4(), repo_id=repo.id, kind=kind, status="queued", stats={}, created_at=now)
    session.add(job)
    await session.commit()
    try:
        enqueued = await arq.enqueue_job(
            function,
            repo_full_name,
            kind,
            str(job.id),
            _job_id=f"{prefix}:{repo_full_name.lower()}",
        )
    except (RedisError, OSError):
        job.status, job.error, job.finished_at = "failed", "enqueue_unavailable", now
        await session.commit()
        raise
    if enqueued is not None:
        return job, True
    await session.delete(job)
    await session.commit()
    related = [key for key, value in JOB_ROUTES.items() if value[1] == prefix]
    query = select(SyncJob).where(SyncJob.repo_id == repo.id, SyncJob.kind.in_(related))
    current = (
        await session.scalars(
            query.where(SyncJob.status.in_(["queued", "running"]))
            .order_by(SyncJob.created_at.desc())
            .limit(1)
        )
    ).first()
    if current is None:
        current = (
            await session.scalars(query.order_by(SyncJob.created_at.desc()).limit(1))
        ).first()
    if current is None:
        raise RuntimeError("Queue and job ledger are inconsistent")
    return current, False

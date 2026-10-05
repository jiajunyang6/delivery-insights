"""Job ledger, arq enqueueing and per-repository Redis locks shared by every worker job."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import structlog
from arq.connections import ArqRedis
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insights.config import REPO_RE
from insights.db.models import Repository, SyncJob
from insights.domain import GitHubAuthError, GitHubError, GitHubNotFoundError
from insights.redis import sync_lock_key

logger = structlog.get_logger(__name__)

JOB_ROUTES = {
    "backfill": ("sync_repo", "sync"),
    "incremental": ("sync_repo", "sync"),
    "rederive": ("rederive_repo", "rederive"),
    "ci_runs": ("enrich_repo", "ci"),
    "ownership": ("enrich_repo", "owners"),
}


async def ensure_repo(session: AsyncSession, repo_full_name: str, now: datetime) -> Repository:
    """Insert a repository if absent and return its case-insensitive identity without committing."""
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
    """Record a ledger row and enqueue the job, deduplicated per repository and route prefix.

    Returns (job, True) when newly queued. Otherwise the new row is dropped and the active (or
    latest) job sharing the prefix is returned with False.
    """
    function, prefix = JOB_ROUTES[kind]
    repo = await ensure_repo(session, repo_full_name, now)
    job = SyncJob(id=uuid4(), repo_id=repo.id, kind=kind, status="queued", stats={}, created_at=now)
    session.add(job)
    # Redis/arq is a separate system: commit the ledger before dispatch so a fast worker can
    # find it. An enqueue failure is recorded explicitly rather than rolling back that ledger.
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


RELEASE_LOCK = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end"
# A TTL may expire and be acquired by another job; renew/release only the original token's lock.
RENEW_LOCK = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('expire', KEYS[1], ARGV[2]) end"
)


def now_for(ctx: dict[str, Any]) -> datetime:
    """Read the injected job clock, defaulting to current aware UTC time."""
    clock = cast(Callable[[], datetime], ctx.get("now", lambda: datetime.now(UTC)))
    return clock()


def sessions_for(ctx: dict[str, Any]) -> async_sessionmaker[AsyncSession]:
    """Return the async database session factory stored in the worker context."""
    return cast(async_sessionmaker[AsyncSession], ctx["session_factory"])


async def set_repo(ctx: dict[str, Any], repo_id: int, **values: Any) -> None:
    """Commit supplied repository fields in a separate short transaction."""
    async with sessions_for(ctx)() as session, session.begin():
        await session.execute(update(Repository).where(Repository.id == repo_id).values(**values))


async def set_job(ctx: dict[str, Any], job_id: str, **values: Any) -> None:
    """Commit supplied sync-job fields in a separate short transaction."""
    async with sessions_for(ctx)() as session, session.begin():
        await session.execute(update(SyncJob).where(SyncJob.id == UUID(job_id)).values(**values))


@asynccontextmanager
async def repository_lock(ctx: dict[str, Any], repo: str, job_id: str) -> AsyncIterator[bool]:
    """Hold the repository's Redis lock for the body; yield False (job marked failed) if taken.

    The lock is renewed while held, a lost lock raises once the body exits, and release only
    deletes the key if this job still owns it.
    """
    redis = cast(ArqRedis, ctx["redis"])
    key = sync_lock_key(repo)
    # The TTL frees a crashed worker's lock; the renewal loop keeps it alive for long jobs.
    acquired = await redis.set(key, job_id, nx=True, ex=7200)
    if not acquired:
        await set_job(
            ctx,
            job_id,
            status="failed",
            finished_at=now_for(ctx),
            error="skipped: repository is locked by another job",
        )
        yield False
        return

    async def renew() -> None:
        """Renew only this job's lock token every ten minutes; raise if ownership is lost."""
        while True:
            await asyncio.sleep(600)
            renewed = await cast(Awaitable[Any], redis.eval(RENEW_LOCK, 1, key, job_id, "7200"))
            if not renewed:
                raise RuntimeError("Repository lock was lost")

    task = asyncio.create_task(renew())
    try:
        yield True
        if task.done():
            task.result()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await cast(Awaitable[Any], redis.eval(RELEASE_LOCK, 1, key, job_id))


async def enqueue_precompute(ctx: dict[str, Any], repo: Repository) -> None:
    """Queue snapshot warming under a deduplicated, case-insensitive repository job ID."""
    await ctx["redis"].enqueue_job(
        "precompute_snapshots",
        repo.full_name,
        _job_id=f"precompute:{repo.full_name.lower()}",
    )


async def last_success(ctx: dict[str, Any], repo_id: int, kind: str) -> datetime | None:
    """Return the latest successful finish time for this repository/job kind, or None."""
    async with sessions_for(ctx)() as session:
        return await session.scalar(
            select(SyncJob.finished_at)
            .where(SyncJob.repo_id == repo_id, SyncJob.kind == kind, SyncJob.status == "succeeded")
            .order_by(SyncJob.finished_at.desc())
            .limit(1)
        )


@dataclass
class JobRun:
    ctx: dict[str, Any]
    repo: Repository
    job_id: str
    kind: str
    stats: dict[str, Any] = field(default_factory=dict)
    result: str = "running"

    async def finish(
        self, result: str = "succeeded", error: str | None = None, *, include_stats: bool = True
    ) -> None:
        """Persist terminal status and permitted stats, then mark this run finished.

        Failure text is capped at 500 characters; failed enrichment preserves existing stats.
        """
        values: dict[str, Any] = {"status": result, "finished_at": now_for(self.ctx)}
        if include_stats and (result == "succeeded" or self.kind not in {"ownership", "ci_runs"}):
            values["stats"] = self.stats
        if error is not None:
            values["error"] = error[:500]
        await set_job(self.ctx, self.job_id, **values)
        self.result = result


@asynccontextmanager
async def run_job(
    ctx: dict[str, Any],
    repo_full_name: str,
    kind: str,
    job_id: str,
) -> AsyncIterator[JobRun | None]:
    """Lock the repository and own the ledger lifecycle of one job; yield None if locked.

    A body that does not finish the job is marked succeeded. Exceptions are recorded as failed
    (and on the repository for sync kinds) and swallowed, unless the job had already finished.
    """
    async with sessions_for(ctx)() as session:
        repo = await ensure_repo(session, repo_full_name, now_for(ctx))
        await session.commit()
    async with repository_lock(ctx, repo_full_name, job_id) as acquired:
        if not acquired:
            yield None
            return
        values: dict[str, Any] = {"status": "running", "started_at": now_for(ctx)}
        if kind in {"rederive", "ci_runs", "ownership"}:
            values["phase"] = kind
        await set_job(ctx, job_id, **values)
        run = JobRun(ctx, repo, job_id, kind)
        try:
            yield run
        except Exception as exc:
            # Follow-up queue failures after completion retain the original success ledger.
            if run.result != "running":
                raise
            sync = kind not in {"rederive", "ci_runs", "ownership"}
            if sync:
                status = (
                    "auth_error"
                    if isinstance(exc, GitHubAuthError)
                    else ("not_found" if isinstance(exc, GitHubNotFoundError) else "failed")
                )
                error = (
                    f"{type(exc).__name__}: {exc}"
                    if isinstance(exc, GitHubError)
                    else type(exc).__name__
                )
                await set_repo(ctx, repo.id, last_sync_status=status, last_sync_error=error[:500])
            else:
                error = (
                    str(exc)
                    if kind != "rederive" and isinstance(exc, GitHubError)
                    else type(exc).__name__
                )
            await run.finish("failed", error)
            event = (
                "sync_failed"
                if sync
                else "rederive_failed"
                if kind == "rederive"
                else "enrichment_failed"
            )
            logger.error(event, repo=repo_full_name, job=job_id, error=error[:500])
        else:
            if run.result == "running":
                await run.finish()

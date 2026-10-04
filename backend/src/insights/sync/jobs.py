import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import structlog
from arq.connections import ArqRedis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insights.config import Settings
from insights.db.models import Repository, SyncJob
from insights.domain import PageResult, RepoRef
from insights.redis import sync_lock_key
from insights.sources.github.adapter import GitHubAdapter
from insights.sources.github.client import GitHubAuthError, GitHubError, GitHubNotFoundError
from insights.sync.derive import current_key, derivation_complete, link_repo
from insights.sync.queue import enqueue_sync, ensure_repo
from insights.sync.store import save_page

logger = structlog.get_logger(__name__)
RELEASE_LOCK = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end"
RENEW_LOCK = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('expire', KEYS[1], ARGV[2]) end"
)


def now_for(ctx: dict[str, Any]) -> datetime:
    clock = cast(Callable[[], datetime], ctx.get("now", lambda: datetime.now(UTC)))
    return clock()


def sessions_for(ctx: dict[str, Any]) -> async_sessionmaker[AsyncSession]:
    return cast(async_sessionmaker[AsyncSession], ctx["session_factory"])


async def set_repo(ctx: dict[str, Any], repo_id: int, **values: Any) -> None:
    async with sessions_for(ctx)() as session, session.begin():
        await session.execute(update(Repository).where(Repository.id == repo_id).values(**values))


async def set_job(ctx: dict[str, Any], job_id: str, **values: Any) -> None:
    async with sessions_for(ctx)() as session, session.begin():
        await session.execute(update(SyncJob).where(SyncJob.id == UUID(job_id)).values(**values))


@asynccontextmanager
async def repository_lock(ctx: dict[str, Any], repo: str, job_id: str) -> AsyncIterator[bool]:
    redis = cast(ArqRedis, ctx["redis"])
    key = sync_lock_key(repo)
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


class SyncRun:
    def __init__(self, ctx: dict[str, Any], repo: Repository, job_id: str) -> None:
        self.ctx, self.repo, self.job_id = ctx, repo, job_id
        self.adapter = cast(GitHubAdapter, ctx["adapter"])
        self.settings = cast(Settings, ctx["settings"])
        self.started = now_for(ctx)
        self.previous_checkpoint = self.started
        self.stats = {
            "prs_fetched": 0,
            "prs_changed": 0,
            "events": 0,
            "pages": 0,
            "graphql_cost": 0,
            "skipped_prs": 0,
            "invariant_violations": 0,
        }

    async def fetch(self, cursor: str | None, *, open_only: bool = False) -> PageResult:
        return await self.adapter.pull_requests_page(
            RepoRef(self.repo.owner, self.repo.name),
            cursor=cursor,
            page_size=self.settings.graphql_page_size,
            open_only=open_only,
        )

    async def store(self, page: PageResult, *, backfill: bool = False) -> None:
        async with sessions_for(self.ctx)() as session, session.begin():
            result = await save_page(
                session, self.repo.id, page, now=now_for(self.ctx), settings=self.settings
            )
            if backfill:
                await session.execute(
                    update(Repository)
                    .where(Repository.id == self.repo.id)
                    .values(backfill_cursor=page.end_cursor)
                )
        self.stats["prs_fetched"] += len(page.prs) + page.skipped_prs
        self.stats["prs_changed"] += result.prs_changed
        self.stats["events"] += result.events
        self.stats["pages"] += 1
        self.stats["graphql_cost"] += page.graphql_cost
        self.stats["skipped_prs"] += page.skipped_prs
        self.stats["invariant_violations"] += result.invariant_violations

    async def pages(
        self,
        cursor: str | None = None,
        *,
        open_only: bool = False,
        backfill: bool = False,
        stop: Callable[[PageResult], bool] | None = None,
        cursor_error: str = "pagination_did_not_advance",
    ) -> AsyncGenerator[PageResult, None]:
        """Overlap one page's download with the preceding page's committed write."""
        seen = {cursor} if cursor else set()
        pending: asyncio.Task[PageResult] | None = asyncio.create_task(
            self.fetch(cursor, open_only=open_only)
        )
        try:
            while pending is not None:
                page = await pending
                pending = None
                invalid_cursor = False
                if page.has_next_page and not (stop and stop(page)):
                    next_cursor = page.end_cursor
                    invalid_cursor = not next_cursor or next_cursor in seen
                    if next_cursor and not invalid_cursor:
                        seen.add(next_cursor)
                        pending = asyncio.create_task(self.fetch(next_cursor, open_only=open_only))
                # Never publish a cursor or consume a prefetched page before this commit succeeds.
                await self.store(page, backfill=backfill)
                yield page
                if invalid_cursor:
                    raise GitHubError(cursor_error)
        finally:
            if pending is not None:
                pending.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await pending

    async def incremental(self, cutoff: datetime) -> None:
        first = True
        watermark = None

        def stop(page: PageResult) -> bool:
            return (
                page.oldest_updated_at is not None
                and page.oldest_updated_at < cutoff - timedelta(minutes=10)
            )

        async with aclosing(self.pages(stop=stop)) as pages:
            async for page in pages:
                if first:
                    watermark, first = page.newest_updated_at, False
        if watermark is not None:
            self.repo.sync_watermark = watermark
            await set_repo(self.ctx, self.repo.id, sync_watermark=watermark)

    async def open_sweep(self) -> None:
        async with aclosing(
            self.pages(open_only=True, cursor_error="open_cursor_did_not_advance")
        ) as pages:
            async for _ in pages:
                pass
        self.repo.last_open_sweep_at = now_for(self.ctx)
        await set_repo(self.ctx, self.repo.id, last_open_sweep_at=self.repo.last_open_sweep_at)

    async def checkpoint(self, **values: Any) -> None:
        checkpoint_time = now_for(self.ctx)
        await self.incremental(self.previous_checkpoint)
        key = current_key(self.settings)
        async with sessions_for(self.ctx)() as session, session.begin():
            if await session.scalar(
                select(Repository.links_pending).where(Repository.id == self.repo.id)
            ):
                await link_repo(session, self.repo.id)
            complete = await derivation_complete(session, self.repo.id, key)
            if complete:
                values["derived_key"] = key
            await session.execute(
                update(Repository)
                .where(Repository.id == self.repo.id)
                .values(last_synced_at=checkpoint_time, **values)
            )
        if not complete:
            async with sessions_for(self.ctx)() as session:
                await enqueue_sync(
                    self.ctx["redis"],
                    session,
                    self.repo.full_name,
                    "rederive",
                    now=now_for(self.ctx),
                )
        self.previous_checkpoint = checkpoint_time

    async def backfill(self) -> None:
        phases = [
            days
            for days in self.settings.backfill_phases
            if (
                self.repo.covered_since is None
                or self.repo.covered_since
                > (self.started - timedelta(days=days)).replace(second=0, microsecond=0)
            )
        ]
        if not phases:
            return
        index = 0
        final_threshold = (self.started - timedelta(days=phases[-1])).replace(
            second=0, microsecond=0
        )

        def stop(page: PageResult) -> bool:
            return page.oldest_updated_at is not None and page.oldest_updated_at < final_threshold

        async with aclosing(
            self.pages(
                self.repo.backfill_cursor,
                backfill=True,
                stop=stop,
                cursor_error="backfill_cursor_did_not_advance",
            )
        ) as pages:
            async for page in pages:
                target = phases[index]
                await set_repo(self.ctx, self.repo.id, backfill_target_days=target)
                await set_job(self.ctx, self.job_id, phase=f"backfill:{target}d")
                if self.repo.sync_watermark is None and page.newest_updated_at:
                    self.repo.sync_watermark = page.newest_updated_at
                    await set_repo(self.ctx, self.repo.id, sync_watermark=self.repo.sync_watermark)
                while index < len(phases):
                    threshold = (self.started - timedelta(days=phases[index])).replace(
                        second=0, microsecond=0
                    )
                    if page.has_next_page and (
                        page.oldest_updated_at is None or page.oldest_updated_at >= threshold
                    ):
                        break
                    if self.repo.last_open_sweep_at is None:
                        await self.open_sweep()
                    self.repo.covered_since = threshold
                    await self.checkpoint(
                        covered_since=threshold, backfill_target_days=phases[index]
                    )
                    index += 1

    async def execute(self) -> None:
        if self.repo.sync_watermark:
            await set_job(self.ctx, self.job_id, phase="incremental")
            await self.incremental(self.repo.sync_watermark)
        await self.backfill()
        if self.repo.last_open_sweep_at is None or self.repo.last_open_sweep_at < (
            now_for(self.ctx) - timedelta(minutes=self.settings.open_sweep_minutes)
        ):
            await self.open_sweep()
        await self.checkpoint(
            last_sync_status="ok",
            last_sync_error=None,
            backfill_target_days=self.settings.backfill_days,
        )


async def sync_repo(ctx: dict[str, Any], repo_full_name: str, kind: str, job_id: str) -> str:
    async with sessions_for(ctx)() as session:
        repo = await ensure_repo(session, repo_full_name, now_for(ctx))
        await session.commit()
    async with repository_lock(ctx, repo_full_name, job_id) as acquired:
        if not acquired:
            return "skipped_locked"
        await set_job(ctx, job_id, status="running", started_at=now_for(ctx))
        run = SyncRun(ctx, repo, job_id)
        log = logger.bind(job=job_id, repo=repo_full_name, phase=kind)
        try:
            if not cast(Settings, ctx["settings"]).github_token:
                await set_repo(
                    ctx,
                    repo.id,
                    last_sync_status="missing_token",
                    last_sync_error="GITHUB_TOKEN is not set",
                )
                await set_job(
                    ctx, job_id, status="failed", error="missing_token", finished_at=now_for(ctx)
                )
                return "missing_token"
            run.adapter.client.page_size = run.settings.graphql_page_size
            run.adapter.client.successful_pages = 0
            await run.execute()
        except Exception as exc:
            status = (
                "auth_error"
                if isinstance(exc, GitHubAuthError)
                else ("not_found" if isinstance(exc, GitHubNotFoundError) else "failed")
            )
            # Only our sanitized upstream errors may include their messages.
            error = (
                f"{type(exc).__name__}: {exc}"
                if isinstance(exc, GitHubError)
                else type(exc).__name__
            )
            await set_repo(ctx, repo.id, last_sync_status=status, last_sync_error=error[:500])
            await set_job(
                ctx,
                job_id,
                status="failed",
                error=error[:500],
                stats=run.stats,
                finished_at=now_for(ctx),
            )
            log.error("sync_failed", error=error[:500])
            return "failed"
        await set_job(ctx, job_id, status="succeeded", stats=run.stats, finished_at=now_for(ctx))
        async with sessions_for(ctx)() as session:
            version = await session.scalar(
                select(Repository.data_version).where(Repository.id == repo.id)
            )
        if version != repo.data_version:
            await ctx["redis"].enqueue_job(
                "precompute_snapshots",
                repo_full_name,
                _job_id=f"precompute:{repo_full_name.lower()}",
            )
        await enqueue_enrichment(ctx, repo)
        log.info("sync_completed", **run.stats)
        return "succeeded"


async def reconcile_tracked_repos(ctx: dict[str, Any]) -> None:
    settings = cast(Settings, ctx["settings"])
    async with sessions_for(ctx)() as session:
        await session.execute(update(Repository).values(tracked=False))
        for full_name in settings.tracked_repo_list:
            repo = await ensure_repo(session, full_name, now_for(ctx))
            repo.tracked = True
            if not settings.github_token:
                repo.last_sync_status = "missing_token"
                repo.last_sync_error = "GITHUB_TOKEN is not set"
        await session.commit()
        repos = (await session.scalars(select(Repository).where(Repository.tracked))).all()
        if settings.github_token:
            for repo in repos:
                if repo.covered_since is None or repo.covered_since > (
                    now_for(ctx) - timedelta(days=settings.backfill_days)
                ):
                    await enqueue_sync(
                        ctx["redis"], session, repo.full_name, "backfill", now=now_for(ctx)
                    )
        else:
            logger.error("github_token_missing", job="startup", repo="tracked", phase="startup")

    from insights.sync.rederive import enqueue_rederivation

    await enqueue_rederivation(ctx)


async def incremental_sync_all(ctx: dict[str, Any]) -> None:
    from insights.sync.rederive import enqueue_rederivation

    await enqueue_rederivation(ctx)
    if not cast(Settings, ctx["settings"]).github_token:
        return
    async with sessions_for(ctx)() as session:
        repos = (await session.scalars(select(Repository).where(Repository.tracked))).all()
        for repo in repos:
            await enqueue_sync(
                ctx["redis"], session, repo.full_name, "incremental", now=now_for(ctx)
            )


async def enqueue_enrichment(ctx: dict[str, Any], repo: Repository) -> None:
    settings = cast(Settings, ctx["settings"])
    async with sessions_for(ctx)() as session:
        for kind, interval in (("ci_runs", timedelta(hours=1)), ("ownership", timedelta(days=1))):
            if kind == "ci_runs" and settings.ci_source != "actions":
                continue
            previous = await session.scalar(
                select(SyncJob.finished_at)
                .where(
                    SyncJob.repo_id == repo.id, SyncJob.kind == kind, SyncJob.status == "succeeded"
                )
                .order_by(SyncJob.finished_at.desc())
                .limit(1)
            )
            if previous is None or previous < now_for(ctx) - interval:
                await enqueue_sync(ctx["redis"], session, repo.full_name, kind, now=now_for(ctx))

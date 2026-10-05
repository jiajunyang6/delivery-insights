"""Sync runs for one repository: staged backfill, incremental windows and open-PR sweeps.

Pages come from the source adapter and are persisted through store.save_page.
"""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing, suppress
from datetime import datetime, timedelta
from typing import Any, cast

import structlog
from sqlalchemy import select, update

from insights.config import Settings
from insights.db.models import Repository
from insights.domain import PageResult, RepoRef
from insights.sources import SourceAdapter
from insights.sources.github.client import GitHubError
from insights.sync.derive import current_key, derivation_complete, enqueue_rederivation
from insights.sync.queue import (
    enqueue_precompute,
    enqueue_sync,
    ensure_repo,
    now_for,
    run_job,
    sessions_for,
    set_job,
    set_repo,
)
from insights.sync.store import save_page

logger = structlog.get_logger(__name__)


class SyncRun:
    """One sync job: resumes the backfill cursor, advances the watermark, records coverage."""

    def __init__(self, ctx: dict[str, Any], repo: Repository, job_id: str) -> None:
        """Bind one repo/job, capture start time and initialize ingestion/derivation counters."""
        self.ctx, self.repo, self.job_id = ctx, repo, job_id
        self.adapter = cast(SourceAdapter, ctx["adapter"])
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
        """Fetch one source page using the run's repository and configured maximum page size."""
        return await self.adapter.pull_requests_page(
            RepoRef(self.repo.owner, self.repo.name),
            cursor=cursor,
            page_size=self.settings.graphql_page_size,
            open_only=open_only,
        )

    async def store(self, page: PageResult, *, backfill: bool = False) -> None:
        """Commit a page's records, derived facts and optional resume cursor in one transaction.

        Stats advance after commit; a failure leaves the previous cursor available for replay.
        """
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
        """Yield committed pages, downloading the next page while the current one is written.

        If more pages remain but the cursor is missing or repeats, raise GitHubError(cursor_error)
        after the last good page has been stored and yielded.
        """
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
        """Re-read PRs updated since `cutoff` and advance the sync watermark."""
        first = True
        watermark = None

        # Read 10 minutes past the cutoff; PRs that did not change are skipped by content hash.
        def stop(page: PageResult) -> bool:
            """Stop once a page reaches before the watermark's ten-minute overlap margin."""
            return (
                page.oldest_updated_at is not None
                and page.oldest_updated_at < cutoff - timedelta(minutes=10)
            )

        async with aclosing(self.pages(stop=stop)) as pages:
            async for page in pages:
                if first:
                    watermark, first = page.newest_updated_at, False
        # Pages are newest-first; save the watermark only after the whole window is stored.
        if watermark is not None:
            self.repo.sync_watermark = watermark
            await set_repo(self.ctx, self.repo.id, sync_watermark=watermark)

    async def open_sweep(self) -> None:
        """Re-read every open PR, including ones not updated within the synced window."""
        async with aclosing(
            self.pages(open_only=True, cursor_error="open_cursor_did_not_advance")
        ) as pages:
            async for _ in pages:
                pass
        self.repo.last_open_sweep_at = now_for(self.ctx)
        await set_repo(self.ctx, self.repo.id, last_open_sweep_at=self.repo.last_open_sweep_at)

    async def checkpoint(self, **values: Any) -> None:
        """Catch up on changes since the previous checkpoint, then publish progress.

        Sets `derived_key` only when every PR is derived with the
        current key (otherwise queues a rederive), and writes `values` with `last_synced_at`.
        """
        # Taken before the catch-up, so last_synced_at never claims changes made during it.
        checkpoint_time = now_for(self.ctx)
        await self.incremental(self.previous_checkpoint)
        key = current_key(self.settings)
        async with sessions_for(self.ctx)() as session, session.begin():
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
        """Walk PRs newest-updated first from the saved cursor through each uncovered phase.

        A phase is checkpointed as `covered_since` only once a stored page reaches past its
        threshold or the listing ends, so coverage never includes a range still downloading.
        """
        # Only phases not yet covered remain (7, 30, then BACKFILL_DAYS); thresholds are rounded
        # to the minute so a resumed run computes the same boundaries.
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
            """Stop once a page's oldest update precedes the final history threshold."""
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
                # One page can complete several short phases at once, so claim each phase whose
                # threshold this page has reached before reading the next page.
                while index < len(phases):
                    threshold = (self.started - timedelta(days=phases[index])).replace(
                        second=0, microsecond=0
                    )
                    if page.has_next_page and (
                        page.oldest_updated_at is None or page.oldest_updated_at >= threshold
                    ):
                        break
                    # The updated-at walk misses open PRs idle since the threshold, so sweep
                    # them before the first coverage claim.
                    if self.repo.last_open_sweep_at is None:
                        await self.open_sweep()
                    self.repo.covered_since = threshold
                    await self.checkpoint(
                        covered_since=threshold, backfill_target_days=phases[index]
                    )
                    index += 1

    async def execute(self) -> None:
        """Resume changes/backfill, refresh open PRs, then publish the final successful checkpoint.

        Completing incremental pagination alone does not establish historical period coverage.
        """
        # Recent changes first, so already covered periods stay fresh while backfill continues.
        if self.repo.sync_watermark:
            await set_job(self.ctx, self.job_id, phase="incremental")
            await self.incremental(self.repo.sync_watermark)
        await self.backfill()
        # Open PRs that nobody touched never show up in the updated-at walk; sweep them hourly.
        if self.repo.last_open_sweep_at is None or self.repo.last_open_sweep_at < (
            now_for(self.ctx) - timedelta(minutes=self.settings.open_sweep_minutes)
        ):
            await self.open_sweep()
        await self.checkpoint(
            last_sync_status="ok",
            last_sync_error=None,
            backfill_target_days=self.settings.backfill_days,
        )


async def reconcile_tracked_repos(ctx: dict[str, Any]) -> None:
    """Reconcile the configured allowlist and queue missing history plus stale derivations.

    Without a GitHub token, record missing_token rather than scheduling source ingestion.
    """
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

    await enqueue_rederivation(ctx)


async def incremental_sync_all(ctx: dict[str, Any]) -> None:
    """Queue stale derivations and, when credentials exist, incremental sync for tracked repos."""
    await enqueue_rederivation(ctx)
    if not cast(Settings, ctx["settings"]).github_token:
        return
    async with sessions_for(ctx)() as session:
        repos = (await session.scalars(select(Repository).where(Repository.tracked))).all()
        for repo in repos:
            await enqueue_sync(
                ctx["redis"], session, repo.full_name, "incremental", now=now_for(ctx)
            )


async def sync_repo(ctx: dict[str, Any], repo_full_name: str, kind: str, job_id: str) -> str:
    """arq entry point for backfill and incremental syncs of one repository.

    Returns the job result, "skipped_locked" or "missing_token". Snapshots are precomputed only
    when the run changed `data_version`.
    """
    async with run_job(ctx, repo_full_name, kind, job_id) as job:
        if job is None:
            return "skipped_locked"
        repo = job.repo
        run = SyncRun(ctx, repo, job_id)
        job.stats = run.stats
        if not run.settings.github_token:
            await set_repo(
                ctx,
                repo.id,
                last_sync_status="missing_token",
                last_sync_error="GITHUB_TOKEN is not set",
            )
            await job.finish("failed", "missing_token", include_stats=False)
            return "missing_token"
        run.adapter.reset()
        await run.execute()
        await job.finish()
        async with sessions_for(ctx)() as session:
            version = await session.scalar(
                select(Repository.data_version).where(Repository.id == repo.id)
            )
        if version != repo.data_version:
            await enqueue_precompute(ctx, repo)
        logger.info("sync_completed", job=job_id, repo=repo_full_name, phase=kind, **run.stats)
    return job.result

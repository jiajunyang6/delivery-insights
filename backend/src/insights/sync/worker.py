from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from insights.config import Settings
from insights.db.engine import create_database
from insights.logging import configure_logging
from insights.sources.github.adapter import GitHubAdapter
from insights.sources.github.client import GitHubClient
from insights.sync.enrichment import sync_ci_runs, sync_ownership
from insights.sync.jobs import incremental_sync_all, reconcile_tracked_repos, sync_repo
from insights.sync.maintenance import housekeeping, precompute_snapshots
from insights.sync.rederive import rederive_repo


async def startup(ctx: dict[str, Any]) -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    engine, sessions = create_database(settings)
    client = GitHubClient(settings, ctx["redis"])
    ctx.update(
        settings=settings,
        engine=engine,
        session_factory=sessions,
        client=client,
        adapter=GitHubAdapter(client),
    )
    await reconcile_tracked_repos(ctx)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["client"].aclose()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        sync_repo,
        rederive_repo,
        precompute_snapshots,
        housekeeping,
        sync_ci_runs,
        sync_ownership,
    ]
    cron_jobs: ClassVar[list[Any]] = [
        cron(incremental_sync_all, minute=set(range(0, 60, Settings().sync_interval_minutes))),
        cron(housekeeping, hour=3, minute=17),
    ]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 1
    job_timeout = 3 * 3600
    keep_result = 0
    redis_settings = RedisSettings.from_dsn(Settings().redis_url)

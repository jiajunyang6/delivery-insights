"""Background maintenance: precompute default snapshots and expire retained data."""

from datetime import timedelta
from typing import Any, cast

import structlog
from redis.exceptions import RedisError
from sqlalchemy import delete

from insights.analytics.dataset import SnapshotParams
from insights.config import Settings, split_list
from insights.db.models import Snapshot, SyncJob
from insights.redis import rows_key, snapshot_key
from insights.snapshots.errors import ResourceError
from insights.snapshots.service import SnapshotService
from insights.sync.queue import now_for, sessions_for

logger = structlog.get_logger(__name__)


async def precompute_snapshots(ctx: dict[str, Any], repo_full_name: str) -> None:
    """Warm the snapshot cache for each configured trailing window ending today.

    Windows that cannot be served yet (ResourceError) are logged and skipped.
    """
    settings = cast(Settings, ctx["settings"])
    now = now_for(ctx)
    service = SnapshotService(sessions_for(ctx), ctx["redis"], settings, now)
    for value in split_list(settings.precompute_days):
        end = now.date()
        start = end - timedelta(days=int(value) - 1)
        params = SnapshotParams(
            (repo_full_name,),
            start,
            end,
            settings.location_dimension,
            settings.directory_depth,
            settings.ci_source,
        )
        try:
            await service.delivery(params)
        except ResourceError as exc:
            logger.info("precompute_skipped", repo=repo_full_name, status=exc.status)


async def housekeeping(ctx: dict[str, Any]) -> None:
    """Delete snapshots older than 7 days and finished job rows older than 30 days.

    Cached snapshot rows and narratives are removed best effort; Redis errors are only logged.
    """
    now = now_for(ctx)
    async with sessions_for(ctx)() as session, session.begin():
        expired = (
            await session.scalars(
                delete(Snapshot)
                .where(Snapshot.created_at <= now - timedelta(days=7))
                .returning(Snapshot.snapshot_id)
            )
        ).all()
        await session.execute(delete(SyncJob).where(SyncJob.finished_at < now - timedelta(days=30)))
    for sid in expired:
        try:
            keys = [snapshot_key(sid), rows_key(sid)]
            async for key in ctx["redis"].scan_iter(match=f"di:narr:{sid}:*", count=100):
                keys.append(key)
            await ctx["redis"].delete(*keys)
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning(
                "housekeeping_cache_unavailable", snapshot_id=sid, error_type=type(exc).__name__
            )
    logger.info("housekeeping_completed", snapshots_deleted=len(expired))

from datetime import timedelta

import pytest
from sqlalchemy import select, update
from tests.factories import at
from tests.integration.test_derive import seed
from tests.integration.test_sync import NOW

from insights.analytics.dataset import SnapshotParams
from insights.db.models import Repository, Snapshot, SyncJob
from insights.redis import snapshot_key
from insights.snapshots.service import SnapshotService
from insights.sync.derive import current_key
from insights.sync.queue import enqueue_sync
from insights.sync.worker import housekeeping, precompute_snapshots

pytestmark = pytest.mark.integration


async def test_housekeeping_deletes_all_cache_keys_and_only_finished_old_jobs(context):
    from insights.db.models import Repository

    await seed(context)
    async with context["session_factory"]() as session:
        await session.execute(
            update(Repository).values(
                covered_since=at(-1000),
                last_synced_at=NOW,
                last_open_sweep_at=NOW,
                derived_key=current_key(context["settings"]),
            )
        )
        await session.commit()
        job, _ = await enqueue_sync(context["redis"], session, "a/b", "incremental", now=NOW)
        job.finished_at = NOW - timedelta(days=31)
        job.status = "succeeded"
        await session.commit()
    params = SnapshotParams(("a/b",), at(0).date(), at(0).date(), sampling_profile="github")
    service = SnapshotService(
        context["session_factory"], context["redis"], context["settings"], NOW
    )
    response = await service.delivery(params)
    sid = response.headers["X-Snapshot-Id"]
    narrative_key = f"di:narr:{sid}:manager:en:v1:template:hash"
    await context["redis"].set(narrative_key, b"test")
    async with context["session_factory"]() as session, session.begin():
        await session.execute(update(Snapshot).values(created_at=NOW - timedelta(days=8)))
    await housekeeping(context)
    assert not await context["redis"].exists(snapshot_key(sid), narrative_key)
    async with context["session_factory"]() as session:
        assert await session.get(Snapshot, sid) is None
        assert await session.get(SyncJob, job.id) is None


async def test_precompute_skips_unready_without_job_rows(context):
    await seed(context)
    await precompute_snapshots(context, "a/b")
    async with context["session_factory"]() as session:
        assert (await session.scalars(select(Snapshot))).all() == []
        assert (await session.scalars(select(SyncJob))).all() == []


async def test_precompute_covers_dashboard_presets(context):
    await seed(context)
    async with context["session_factory"]() as session, session.begin():
        await session.execute(
            update(Repository).values(
                covered_since=at(-1000),
                last_synced_at=NOW,
                last_open_sweep_at=NOW,
                derived_key=current_key(context["settings"]),
            )
        )
    await precompute_snapshots(context, "a/b")
    async with context["session_factory"]() as session:
        snapshots = (await session.scalars(select(Snapshot))).all()
        assert sorted((row.period_to - row.period_from).days + 1 for row in snapshots) == [
            7,
            30,
            60,
        ]
        assert all(row.period_to == NOW.date() for row in snapshots)

from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import select, text, update
from tests.factories import at
from tests.integration.test_derive import seed
from tests.integration.test_sync import NOW

from insights.analytics.dataset import SnapshotParams
from insights.analytics.snapshot import build_snapshot
from insights.db.dataset import load_dataset
from insights.db.models import Repository
from insights.sync.derive import current_key, link_repo
from insights_eval.generator import SyntheticRepo
from insights_eval.pipeline import dataset_from_repo

pytestmark = pytest.mark.integration


async def test_readonly_repeatable_snapshot_matches_pure_pipeline(context):
    repo_id, _, page = await seed(context, 5)
    async with context["session_factory"]() as session, session.begin():
        await link_repo(session, repo_id)
        await session.execute(
            update(Repository).values(
                covered_since=at(-5000),
                last_synced_at=NOW,
                last_sync_status="ok",
                derived_key=current_key(context["settings"]),
            )
        )
    params = SnapshotParams(("a/b",), date(2026, 1, 1), date(2026, 1, 1))
    async with context["session_factory"]() as session:
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        await session.execute(text("SET TRANSACTION READ ONLY"))
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        original_version = await session.scalar(select(Repository.data_version))
        async with context["session_factory"]() as writer, writer.begin():
            await writer.execute(
                update(Repository).values(data_version=Repository.data_version + 1)
            )
        data = await load_dataset(session, params, now=NOW)
        assert data.repos[0].data_version == original_version
        assert len(data.prs) == 5
        assert len(data.reviews) == 5
        await session.rollback()
    synthetic = SyntheticRepo(
        "a/b", "main", page.prs, (), params.period_from, params.period_to, NOW
    )
    pure = dataset_from_repo(synthetic, covered_since=at(-5000))
    pure = replace(pure, repos=(replace(pure.repos[0], data_version=original_version),))
    assert build_snapshot(data, params=params) == build_snapshot(pure, params=params)

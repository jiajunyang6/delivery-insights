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
from insights.db.models import OwnershipRule, Repository
from insights.snapshots.service import SnapshotService
from insights.sync.derive import current_key, link_repo
from insights_eval.generator import SyntheticRepo
from insights_eval.pipeline import dataset_from_repo

pytestmark = pytest.mark.integration


async def test_metadata_reuse_retains_codeowners_and_area_owner_counts(context):
    repo_id, _, _ = await seed(context, 35)
    async with context["session_factory"]() as session, session.begin():
        await link_repo(session, repo_id)
        await session.execute(
            update(Repository).values(
                covered_since=at(-5000),
                last_synced_at=NOW,
                last_open_sweep_at=NOW,
                last_sync_status="ok",
                derived_key=current_key(context["settings"]),
            )
        )
        session.add_all(
            [
                OwnershipRule(
                    repo_id=repo_id,
                    source="area_owners",
                    pattern="area-A",
                    owners=["alice", "bob"],
                    line_no=1,
                ),
                OwnershipRule(
                    repo_id=repo_id,
                    source="codeowners",
                    pattern="src/*",
                    owners=["carol"],
                    line_no=1,
                ),
            ]
        )
    params = SnapshotParams(("a/b",), date(2026, 1, 1), date(2026, 1, 1))
    service = SnapshotService(
        context["session_factory"], context["redis"], context["settings"], NOW
    )
    async with context["session_factory"]() as session:
        metadata = await service.metadata(session, params)
        direct = await load_dataset(session, params, now=NOW)
        reused = await load_dataset(session, params, now=NOW, metadata=metadata)
    assert reused == direct
    assert reused.repos[0].owners == (("area-A", 2), ("codeowners:src/*", 1))
    snapshot = build_snapshot(reused, params=params)
    assert snapshot["bottleneck_analysis"]["locations"][0]["owners_count"] == 2


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


async def test_database_activity_scope_matches_pure_pipeline(context):
    from tests.unit.test_period_scope import period_repo

    from insights.domain import PageResult, RepositoryInfo
    from insights.sync.queue import ensure_repo
    from insights.sync.store import save_page

    syn = period_repo()
    async with context["session_factory"]() as session, session.begin():
        repo = await ensure_repo(session, "a/b", NOW)
        page = PageResult(
            RepositoryInfo("a/b", "main", False), syn.records, None, False, at(60), at(60), 1
        )
        await save_page(session, repo.id, page, now=at(72), settings=context["settings"])
        repo.covered_since = at(-5000)
        repo.last_synced_at = at(72)
        repo.last_sync_status = "ok"
        version = repo.data_version
    params = SnapshotParams(("a/b",), syn.period_from, syn.period_to)
    async with context["session_factory"]() as session:
        loaded = await load_dataset(session, params, now=at(72))
    pure = dataset_from_repo(syn, covered_since=at(-5000))
    pure = replace(pure, repos=(replace(pure.repos[0], data_version=version),))
    assert {p.number for p in loaded.flow} == {2, 5, 8}
    assert build_snapshot(loaded, params=params) == build_snapshot(pure, params=params)

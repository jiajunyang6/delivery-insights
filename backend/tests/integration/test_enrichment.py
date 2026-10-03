from dataclasses import replace
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update
from tests.factories import at, event, record
from tests.integration.test_derive import seed
from tests.integration.test_sync import NOW, queued
from tests.unit.test_ci import run

from insights.analytics.dataset import SnapshotParams
from insights.analytics.snapshot import build_snapshot
from insights.db.ci import load_ci_data
from insights.db.dataset import load_dataset
from insights.db.models import PrFact, PrInterval, Repository, SyncJob
from insights.domain import OwnershipRule
from insights.sync.derive import current_key, link_repo
from insights.sync.enrichment import save_ownership, save_runs, sync_ci_runs, sync_ownership
from insights.sync.jobs import enqueue_enrichment
from insights.sync.rederive import rederive_repo
from insights.sync.store import save_page
from insights_eval.generator import SyntheticRepo
from insights_eval.pipeline import dataset_from_repo

pytestmark = pytest.mark.integration


async def test_ci_number_and_sha_mapping_rederive_idempotency_and_pipeline_parity(context):
    repo_id, _, page = await seed(context, 2)
    records = (
        record(number=1, source_id="PR1", events=(event("commit", 0),)),
        record(number=2, source_id="PR2"),
    )
    page = replace(page, prs=records)
    runs = [run(1, 0, 5, pr_numbers=()), run(2, 1, 6, pr_numbers=(2,), head_sha="b" * 40)]
    async with context["session_factory"]() as session, session.begin():
        await save_page(session, repo_id, page, now=NOW, settings=context["settings"])
        await link_repo(session, repo_id)
        before = await session.scalar(select(Repository.data_version))
        assert await save_runs(session, repo_id, runs, settings=context["settings"], now=NOW) == (
            2,
            2,
            0,
        )
        assert await session.scalar(select(Repository.data_version)) == before + 1
        assert (await session.scalars(select(PrFact.ci_covered))).all() == [True, True]
        mapped = await load_ci_data(session, [repo_id], flow_only=True)
        assert len(mapped.by_pr) == 2 and mapped.runs[0][1].pr_numbers == (1,)
        assert (
            await session.scalars(select(PrInterval).where(PrInterval.state == "waiting_ci"))
        ).all()
        assert await save_runs(session, repo_id, runs, settings=context["settings"], now=NOW) == (
            0,
            0,
            0,
        )
        assert await session.scalar(select(Repository.data_version)) == before + 1
        await session.execute(
            update(Repository).values(
                covered_since=at(-5000),
                last_synced_at=NOW,
                last_sync_status="ok",
                derived_key=current_key(context["settings"]),
            )
        )
    params = SnapshotParams(("A/B",), date(2026, 1, 1), date(2026, 1, 1), ci_source="actions")
    async with context["session_factory"]() as session:
        data = await load_dataset(session, params, now=NOW)
    syn = SyntheticRepo(
        "A/B", "main", records, tuple(runs), params.period_from, params.period_to, NOW
    )
    pure = dataset_from_repo(syn, covered_since=at(-5000))
    pure = replace(pure, repos=(replace(pure.repos[0], data_version=before + 1),))
    assert build_snapshot(data, params=params) == build_snapshot(pure, params=params)
    async with context["session_factory"]() as session, session.begin():
        moved = replace(runs[0], head_sha="unknown", pr_numbers=(2,))
        assert await save_runs(
            session, repo_id, [moved], settings=context["settings"], now=NOW
        ) == (1, 2, 0)
        facts = (await session.scalars(select(PrFact).order_by(PrFact.number))).all()
        assert not facts[0].ci_covered and facts[1].ci_covered


async def test_ownership_invalidates_codeonly_rederives_and_area_changes_snapshot(context):
    repo_id, _, page = await seed(context)
    page = replace(page, prs=(replace(page.prs[0], labels=()),))
    async with context["session_factory"]() as session, session.begin():
        await save_page(session, repo_id, page, now=NOW, settings=context["settings"])
        await session.execute(
            update(Repository).values(
                covered_since=at(-5000),
                last_synced_at=NOW,
                derived_key=current_key(context["settings"]),
            )
        )
    rules = [
        OwnershipRule("codeowners", "/src/", ("@a", "@b"), 1),
        OwnershipRule("area_owners", "area-A", ("@a",), 1),
    ]
    context["adapter"].ownership_rules = AsyncMock(return_value=rules)
    job, _ = await queued(context, "ownership")
    assert await sync_ownership(context, "a/b", "ownership", str(job.id)) == "succeeded"
    async with context["session_factory"]() as session:
        assert await session.scalar(select(Repository.derived_key)) is None
        assert await session.scalar(select(PrFact.derive_key)) is None
        pending = (await session.scalars(select(SyncJob).where(SyncJob.kind == "rederive"))).one()
    assert await rederive_repo(context, "a/b", "rederive", str(pending.id)) == "succeeded"
    async with context["session_factory"]() as session, session.begin():
        assert await session.scalar(select(PrFact.locations)) == ["codeowners:/src/"]
        version = await session.scalar(select(Repository.data_version))
        assert await save_ownership(session, repo_id, rules, now=NOW) == (False, False)
        assert await session.scalar(select(Repository.data_version)) == version
        rules[1] = replace(rules[1], owners=("@a", "@b"))
        assert await save_ownership(session, repo_id, rules, now=NOW) == (False, True)
        assert await session.scalar(select(Repository.derived_key)) == current_key(
            context["settings"]
        )
        assert await session.scalar(select(PrFact.derive_key)) == current_key(context["settings"])
    async with context["session_factory"]() as session:
        data = await load_dataset(
            session, SnapshotParams(("a/b",), date(2026, 1, 1), date(2026, 1, 1)), now=NOW
        )
        assert dict(data.repos[0].owners) == {"area-A": 2, "codeowners:/src/": 2}


async def test_first_ci_sync_backfills_then_uses_two_days_and_scheduler_cooldowns(context):
    repo_id, _, _ = await seed(context)
    async with context["session_factory"]() as session, session.begin():
        await session.execute(update(Repository).values(covered_since=at(-5000)))
    context["adapter"].ci_runs = AsyncMock(return_value=[])
    job, _ = await queued(context, "ci_runs")
    assert await sync_ci_runs(context, "a/b", "ci_runs", str(job.id)) == "succeeded"
    assert context["adapter"].ci_runs.call_args.kwargs["created_from"] == at(-5000)
    # A distinct later job sees the prior success; replaying the same ledger ID is not a new run.
    await context["redis"].flushdb()
    job2, _ = await queued(context, "ci_runs")
    assert await sync_ci_runs(context, "a/b", "ci_runs", str(job2.id)) == "succeeded"
    assert context["adapter"].ci_runs.call_args.kwargs["created_from"] == NOW - timedelta(days=2)
    async with context["session_factory"]() as session:
        repo = await session.get(Repository, repo_id)
    await enqueue_enrichment(context, repo)
    async with context["session_factory"]() as session:
        pending = (await session.scalars(select(SyncJob).where(SyncJob.status == "queued"))).all()
        assert [j.kind for j in pending] == ["ownership"]
    context["settings"].ci_source = "none"
    context["now"] = lambda: NOW + timedelta(days=3)
    await enqueue_enrichment(context, repo)
    async with context["session_factory"]() as session:
        assert not (
            await session.scalars(
                select(SyncJob).where(SyncJob.kind == "ci_runs", SyncJob.status == "queued")
            )
        ).all()

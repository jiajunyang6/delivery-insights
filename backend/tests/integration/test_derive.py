from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update
from tests.factories import at, event, record
from tests.integration.test_sync import NOW, queued

from insights.analytics.timeline import Interval, TimelineResult, check_invariants, pr_input
from insights.db.models import PrFact, PrInterval, PullRequest, Repository, SyncJob
from insights.domain import PageResult, RepositoryInfo
from insights.sync.derive import current_key, derivation_complete, derive_prs
from insights.sync.jobs import incremental_sync_all, reconcile_tracked_repos
from insights.sync.queue import ensure_repo
from insights.sync.rederive import rederive_repo
from insights.sync.store import save_page

pytestmark = pytest.mark.integration


async def seed(ctx, count=1):
    records = tuple(
        record(
            number=i,
            source_id=f"PR{i}",
            title=f"Title {i}",
            events=(
                event("review", 2, review_id=f"r{i}"),
                event("review_dismissed", 4, review_id=f"r{i}"),
            ),
        )
        for i in range(1, count + 1)
    )
    page = PageResult(RepositoryInfo("a/b", "main", False), records, None, False, at(10), at(10), 1)
    async with ctx["session_factory"]() as session, session.begin():
        repo = await ensure_repo(session, "a/b", NOW)
        saved = await save_page(session, repo.id, page, now=NOW, settings=ctx["settings"])
        repo_id = repo.id
    return repo_id, saved, page


async def test_transactional_derivation_and_preserved_links(context):
    repo_id, saved, page = await seed(context)
    async with context["session_factory"]() as session, session.begin():
        fact = await session.get(PrFact, saved.pr_ids[0])
        assert fact.derive_key == current_key(context["settings"])
        rows = (await session.scalars(select(PrInterval).order_by(PrInterval.seq))).all()
        assert [r.state for r in rows] == ["waiting_reviewer", "waiting_merge", "waiting_reviewer"]
        result = TimelineResult(
            fact.ready_at,
            tuple(Interval(r.state, r.start_at, r.end_at) for r in rows),
            fact.approved_at,
            fact.review_rounds,
            fact.state_at_close,
        )
        assert not check_invariants(result, pr_input(page.prs[0]))
        fact.is_reland = True
        await session.flush()
        await derive_prs(session, saved.pr_ids, settings=context["settings"], now=NOW)
        await session.refresh(fact)
        assert fact.is_reland
        assert await derivation_complete(session, repo_id, current_key(context["settings"]))
        again = await save_page(session, repo_id, page, now=NOW, settings=context["settings"])
        assert not again.prs_changed
        assert await session.scalar(select(func.count()).select_from(PrInterval)) == 3


async def test_rederive_change_and_noop_without_github(context):
    repo_id, saved, _ = await seed(context)
    context["settings"].github_token = None
    async with context["session_factory"]() as session, session.begin():
        await session.execute(
            update(Repository).values(
                covered_since=at(-100), derived_key=current_key(context["settings"])
            )
        )
        version = await session.scalar(select(Repository.data_version))
    context["settings"].location_dimension = "directory"
    await reconcile_tracked_repos(context)
    async with context["session_factory"]() as session:
        job = (await session.scalars(select(SyncJob).where(SyncJob.kind == "rederive"))).one()
    assert await rederive_repo(context, "a/b", "rederive", str(job.id)) == "succeeded"
    async with context["session_factory"]() as session:
        repo = await session.get(Repository, repo_id)
        fact = await session.get(PrFact, saved.pr_ids[0])
        assert repo.derived_key == current_key(context["settings"])
        assert repo.data_version == version + 1
        assert fact.locations == ["dir:src/a.py"]
    assert await rederive_repo(context, "a/b", "rederive", str(job.id)) == "succeeded"
    async with context["session_factory"]() as session:
        assert await session.scalar(select(Repository.data_version)) == version + 1


async def test_partial_failure_keyset_resume_and_cron_without_token(context, monkeypatch):
    import insights.sync.rederive as module

    repo_id, _, _ = await seed(context, 501)
    context["settings"].github_token = None
    async with context["session_factory"]() as session, session.begin():
        await session.execute(update(Repository).values(covered_since=at(-100), derived_key="old"))
        await session.execute(update(PrFact).values(derive_key="old"))
        version = await session.scalar(select(Repository.data_version))
    job, _ = await queued(context, "rederive")
    original = module.derive_prs
    calls = []

    async def fail_second(session, ids, **kwargs):
        calls.append(len(ids))
        if len(calls) == 2:
            raise RuntimeError("Injected failure")
        await original(session, ids, **kwargs)

    monkeypatch.setattr(module, "derive_prs", fail_second)
    assert await rederive_repo(context, "a/b", "rederive", str(job.id)) == "failed"
    assert calls == [500, 1]
    async with context["session_factory"]() as session:
        assert await session.scalar(select(Repository.derived_key)) == "old"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(PrFact)
                .where(PrFact.derive_key == current_key(context["settings"]))
            )
            == 500
        )
    await context["redis"].flushdb()
    await incremental_sync_all(context)
    async with context["session_factory"]() as session:
        pending = (await session.scalars(select(SyncJob).where(SyncJob.status == "queued"))).one()
    spy = AsyncMock(wraps=original)
    monkeypatch.setattr(module, "derive_prs", spy)
    assert await rederive_repo(context, "a/b", "rederive", str(pending.id)) == "succeeded"
    assert len(spy.call_args.args[1]) == 1
    async with context["session_factory"]() as session:
        assert await session.scalar(select(Repository.data_version)) == version + 1
        assert await derivation_complete(session, repo_id, current_key(context["settings"]))


async def test_missing_fact_is_pending(context):
    repo_id, _, _ = await seed(context)
    async with context["session_factory"]() as session, session.begin():
        from sqlalchemy import delete

        await session.execute(delete(PrFact))
        assert not await derivation_complete(session, repo_id, current_key(context["settings"]))
        assert await session.scalar(select(func.count()).select_from(PullRequest)) == 1


async def test_checkpoint_rejects_incomplete_derivation(context):
    from insights.sync.jobs import SyncRun

    repo_id, _, _ = await seed(context)
    async with context["session_factory"]() as session, session.begin():
        await session.execute(update(PrFact).values(derive_key="old"))
        repo = await session.get(Repository, repo_id)
    job, _ = await queued(context)
    run = SyncRun(context, repo, str(job.id))
    run.incremental = AsyncMock()
    await run.checkpoint(covered_since=at(-100))
    async with context["session_factory"]() as session:
        assert await session.scalar(select(Repository.derived_key)) is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SyncJob)
                .where(SyncJob.kind == "rederive", SyncJob.status == "queued")
            )
            == 1
        )

"""Regression coverage for prefetch freshness and deferred PR linking."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import orjson
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, update
from tests.factories import at, event, record
from tests.integration.test_sync import NOW, load_state, queued

from insights.db.models import PrEvent, PrFact, PrFile, PrInterval, PullRequest, Repository
from insights.domain import PageResult, RepoRef, RepositoryInfo
from insights.sync.derive import current_key, link_repo
from insights.sync.jobs import SyncRun
from insights.sync.queue import ensure_repo
from insights.sync.store import save_page

pytestmark = pytest.mark.integration


def page(*records, cursor=None, more=False):
    dates = [pr.updated_at for pr in records]
    return PageResult(
        RepositoryInfo("a/b", "main", False),
        tuple(records),
        cursor,
        more,
        min(dates, default=None),
        max(dates, default=None),
        1,
    )


def revert(merged=True):
    return record(
        source_id="PR2",
        number=2,
        title='Revert "Change"',
        body_excerpt="Reverts a/b#1",
        created_at=at(20),
        updated_at=at(30),
        state="MERGED" if merged else "OPEN",
        merged_at=at(30) if merged else None,
        closed_at=at(30) if merged else None,
    )


async def save(ctx, repo_id, result):
    async with ctx["session_factory"]() as session, session.begin():
        return await save_page(session, repo_id, result, now=ctx["now"](), settings=ctx["settings"])


async def finalize(ctx, repo_id, job_id):
    async with ctx["session_factory"]() as session:
        repo = await session.get(Repository, repo_id)
    run = SyncRun(ctx, repo, str(job_id))
    run.incremental = AsyncMock()
    await run.checkpoint()


@pytest.mark.parametrize("legacy_revision", ["0001_initial", "0002_links_pending"])
async def test_upgrade_existing_data_keeps_pending_links(context, legacy_revision):
    job, _ = await queued(context)
    repo, _ = await load_state(context)
    saved = await save(context, repo.id, page(record(), revert()))
    async with context["session_factory"]() as session, session.begin():
        await session.execute(
            update(Repository).values(
                covered_since=at(-100),
                derived_key=current_key(context["settings"]),
                last_synced_at=NOW - timedelta(hours=1),
                last_sync_status="failed",
            )
        )
        version = await session.scalar(select(Repository.data_version))
        empty = await ensure_repo(session, "empty/repo", NOW)
        empty_id = empty.id
    # Verify both pre-flag databases and databases that already applied the old flag migration.
    await context["engine"].dispose()
    await asyncio.to_thread(command.downgrade, Config("alembic.ini"), legacy_revision)
    if legacy_revision == "0002_links_pending":
        async with context["session_factory"]() as session, session.begin():
            await session.execute(update(Repository).values(links_pending=False))
    await context["engine"].dispose()
    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")
    async with context["session_factory"]() as session:
        upgraded = await session.get(Repository, repo.id)
        assert upgraded.data_version == version
        assert (
            await session.scalars(select(PullRequest.id).order_by(PullRequest.number))
        ).all() == list(saved.pr_ids)
        assert upgraded.links_pending is True
        assert not (await session.get(Repository, empty_id)).links_pending
    # Simulate the next sync seeing no new PRs; all fact identities are already current.
    await finalize(context, repo.id, job.id)
    async with context["session_factory"]() as session:
        original = await session.get(PrFact, saved.pr_ids[0])
        assert original.reverted_by_pr_id == saved.pr_ids[1]
        assert original.reverted_at == at(30)
        assert not (await session.get(Repository, repo.id)).links_pending


async def test_existing_revert_merge_relinks_original(context):
    job, _ = await queued(context)
    repo, _ = await load_state(context)
    saved = await save(context, repo.id, page(record(), revert(merged=False)))
    await finalize(context, repo.id, job.id)
    async with context["session_factory"]() as session:
        assert not (await session.get(Repository, repo.id)).links_pending
        assert (await session.get(PrFact, saved.pr_ids[0])).reverted_by_pr_id is None
    changed = await save(context, repo.id, page(revert()))
    assert changed.prs_created == 0 and changed.prs_changed == 1
    await finalize(context, repo.id, job.id)
    async with context["session_factory"]() as session:
        original = await session.get(PrFact, saved.pr_ids[0])
        assert original.reverted_by_pr_id == saved.pr_ids[1]
        assert original.reverted_at == at(30)
        assert not (await session.get(Repository, repo.id)).links_pending


async def test_stale_page_preserves_events_files_facts_and_data_version(context):
    job, _ = await queued(context)
    repo, _ = await load_state(context)
    fresh = record(
        title="fresh", updated_at=at(40), files=("fresh.py",), events=(event("review", 2),)
    )
    stale = replace(fresh, title="stale", updated_at=at(20), files=("old.py",), events=())
    saved = await save(context, repo.id, page(fresh))
    await finalize(context, repo.id, job.id)
    async with context["session_factory"]() as session:
        version = (await session.get(Repository, repo.id)).data_version
        computed_at = (await session.get(PrFact, saved.pr_ids[0])).computed_at
        intervals = (await session.scalars(select(PrInterval))).all()
        expected_intervals = [(row.seq, row.state, row.start_at, row.end_at) for row in intervals]
    ignored = await save(context, repo.id, page(stale))
    assert ignored.prs_changed == ignored.prs_created == ignored.events == 0
    assert ignored.pr_ids == ()
    async with context["session_factory"]() as session:
        assert (await session.get(Repository, repo.id)).data_version == version
        assert not (await session.get(Repository, repo.id)).links_pending
        assert (await session.get(PullRequest, saved.pr_ids[0])).title == "fresh"
        assert (await session.scalars(select(PrFile.path))).all() == ["fresh.py"]
        assert len((await session.scalars(select(PrEvent))).all()) == 1
        assert (await session.get(PrFact, saved.pr_ids[0])).computed_at == computed_at
        intervals = (await session.scalars(select(PrInterval))).all()
        assert [
            (row.seq, row.state, row.start_at, row.end_at) for row in intervals
        ] == expected_intervals


async def test_failed_linking_retains_pending_work_for_retry(context, monkeypatch):
    import insights.sync.jobs as jobs

    job, _ = await queued(context)
    repo, _ = await load_state(context)
    saved = await save(context, repo.id, page(record(), revert()))

    async def fail_after_link(session, repo_id):
        await link_repo(session, repo_id)
        raise RuntimeError("link rollback")

    monkeypatch.setattr(jobs, "link_repo", fail_after_link)
    with pytest.raises(RuntimeError, match="link rollback"):
        await finalize(context, repo.id, job.id)
    async with context["session_factory"]() as session:
        assert (await session.get(Repository, repo.id)).links_pending
        assert (await session.get(PrFact, saved.pr_ids[0])).reverted_by_pr_id is None
    monkeypatch.setattr(jobs, "link_repo", link_repo)
    await finalize(context, repo.id, job.id)
    async with context["session_factory"]() as session:
        assert not (await session.get(Repository, repo.id)).links_pending
        assert (await session.get(PrFact, saved.pr_ids[0])).reverted_by_pr_id == saved.pr_ids[1]


@pytest.mark.parametrize("prefetch", [False, True])
async def test_prefetch_cannot_overwrite_checkpoint_catchup(context, prefetch):
    job, _ = await queued(context)
    repo, _ = await load_state(context)
    clock = [NOW]
    context["now"] = lambda: clock[0]
    run = SyncRun(context, repo, str(job.id))
    downloaded = asyncio.Event()
    old = record(
        source_id="PR2",
        number=2,
        title="stale",
        state="OPEN",
        merged_at=None,
        closed_at=None,
        created_at=NOW - timedelta(days=42),
        updated_at=NOW - timedelta(days=40),
    )
    fresh = replace(old, title="fresh from checkpoint", updated_at=NOW + timedelta(minutes=1))
    first = record(
        updated_at=NOW - timedelta(days=9),
        created_at=NOW - timedelta(days=11),
        merged_at=NOW - timedelta(days=9),
        closed_at=NOW - timedelta(days=9),
    )
    tail = replace(
        first,
        source_id="PR3",
        number=3,
        created_at=NOW - timedelta(days=132),
        updated_at=NOW - timedelta(days=130),
        closed_at=NOW - timedelta(days=130),
        merged_at=NOW - timedelta(days=130),
    )
    # A full first page older than the next 10-minute cutoff; PR2 sorts onto page two.
    recent = [
        replace(
            old,
            source_id=f"PR{i}",
            number=i,
            title="Other update",
            updated_at=NOW + timedelta(minutes=5),
        )
        for i in range(4, 29)
    ]
    initial_calls = 0
    requests = []

    async def fetch(cursor, **kwargs):
        nonlocal initial_calls
        requests.append(cursor)
        if cursor == "p1":
            downloaded.set()
            return page(old if clock[0] == NOW else fresh, cursor="p2", more=True)
        if cursor == "p2":
            return page(tail)
        if cursor == "catchup-next":
            return page(fresh)
        initial_calls += 1
        if initial_calls == 1:
            return page(first, cursor="p1", more=True)
        return page(*recent, cursor="catchup-next", more=True)

    async def open_sweep():
        if prefetch:
            await asyncio.wait_for(downloaded.wait(), 2)
        await run.store(page(fresh))
        # A large open sweep or rate-limit wait can take longer than ten minutes.
        clock[0] = NOW + timedelta(minutes=20)
        run.repo.last_open_sweep_at = clock[0]

    async def serial_pages(cursor=None, *, stop=None, backfill=False, **kwargs):
        while True:
            result = await fetch(cursor)
            await run.store(result, backfill=backfill)
            yield result
            if not result.has_next_page or (stop and stop(result)):
                break
            cursor = result.end_cursor

    run.fetch, run.open_sweep = fetch, open_sweep
    if not prefetch:
        run.pages = serial_pages
    await run.execute()
    async with context["session_factory"]() as session:
        stored = (await session.scalars(select(PullRequest).where(PullRequest.number == 2))).one()
        published = await session.get(Repository, repo.id)
        assert published.last_synced_at == clock[0]
        assert published.covered_since == NOW - timedelta(days=context["settings"].backfill_days)
        assert requests.count("p1") == requests.count("p2") == 1
        # Only the first checkpoint's wider cutoff reaches PR2; later catchups stop at page one.
        assert requests.count("catchup-next") == 1
        assert stored.title == fresh.title, "Prefetched stale page overwrote completed catchup"
        assert stored.updated_at == fresh.updated_at


async def test_second_failure_restarts_success_streak(context, github_page):
    client = context["adapter"].client
    client.page_size = 12
    route = (
        context["router"]
        .post("https://api.github.com/graphql")
        .mock(
            side_effect=[
                httpx.Response(200, json=github_page),
                httpx.Response(200, json={"errors": [{"message": "Something went wrong"}]}),
                httpx.Response(200, json=github_page),
                httpx.Response(200, json=github_page),
                httpx.Response(200, json=github_page),
            ]
        )
    )
    adapter = context["adapter"]
    await adapter.pull_requests_page(RepoRef("a", "b"), cursor="a", page_size=25)
    assert client.successful_pages == 1 and client.page_size == 12
    await adapter.pull_requests_page(RepoRef("a", "b"), cursor="b", page_size=25)
    assert client.successful_pages == 1 and client.page_size == 6
    await adapter.pull_requests_page(RepoRef("a", "b"), cursor="c", page_size=25)
    assert client.successful_pages == 2 and client.page_size == 25
    await adapter.pull_requests_page(RepoRef("a", "b"), cursor="d", page_size=25)
    variables = [orjson.loads(call.request.content)["variables"] for call in route.calls]
    assert [v["pageSize"] for v in variables] == [12, 12, 6, 6, 25]
    assert [v["cursor"] for v in variables] == ["a", "b", "b", "c", "d"]

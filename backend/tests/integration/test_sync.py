from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from arq.connections import RedisSettings, create_pool
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from insights.config import Settings
from insights.db.models import PrEvent, PrFile, PullRequest, Repository, SyncJob
from insights.redis import sync_lock_key
from insights.sources.github.adapter import GitHubAdapter
from insights.sources.github.client import GitHubClient
from insights.sync.jobs import incremental_sync_all, reconcile_tracked_repos, sync_repo
from insights.sync.queue import enqueue_sync

pytestmark = pytest.mark.integration
NOW = datetime(2026, 3, 2, tzinfo=UTC)


@pytest.fixture
async def context(migrated_database, redis_url, respx_mock):
    settings = Settings(
        database_url=migrated_database,
        redis_url=redis_url,
        tracked_repos="a/b",
        github_token=SecretStr("ghp_" + "x" * 36),
    )
    engine = create_async_engine(migrated_database)
    redis = await create_pool(RedisSettings.from_dsn(redis_url))
    await redis.flushdb()
    client = GitHubClient(settings, redis, sleep=AsyncMock())
    ctx = {
        "settings": settings,
        "engine": engine,
        "session_factory": async_sessionmaker(engine, expire_on_commit=False),
        "redis": redis,
        "adapter": GitHubAdapter(client),
        "now": lambda: NOW,
        "router": respx_mock,
    }
    yield ctx
    await client.aclose()
    await redis.aclose()
    await engine.dispose()


def response_page(fixture, number, days, cursor=None, more=False, title=None):
    page = deepcopy(fixture)
    node = page["data"]["repository"]["pullRequests"]["nodes"][0]
    node["id"], node["number"] = f"PR_{number}", number
    node["updatedAt"] = (NOW - timedelta(days=days)).isoformat()
    node["createdAt"] = (NOW - timedelta(days=days + 2)).isoformat()
    node["title"] = title or f"Change {number}"
    node["timelineItems"]["nodes"][0]["id"] = f"C{number}"
    page["data"]["repository"]["pullRequests"]["pageInfo"] = {
        "hasNextPage": more,
        "endCursor": cursor,
    }
    return page


async def queued(ctx, kind="backfill"):
    async with ctx["session_factory"]() as session:
        return await enqueue_sync(ctx["redis"], session, "a/b", kind, now=NOW)


async def load_state(ctx):
    async with ctx["session_factory"]() as session:
        repo = (await session.scalars(select(Repository))).one()
        jobs = (await session.scalars(select(SyncJob).order_by(SyncJob.created_at))).all()
        return repo, jobs


async def test_staged_backfill_sweep_catchup_and_idempotency(context, github_page):
    import orjson

    requests = []
    initial_calls = 0

    def respond(request):
        nonlocal initial_calls
        variables = orjson.loads(request.content)["variables"]
        requests.append(variables)
        if variables["states"] == ["OPEN"]:
            page = response_page(github_page, 4, 365)
        elif variables["cursor"] == "p1":
            page = response_page(github_page, 2, 40, cursor="p2", more=True)
        elif variables["cursor"] == "p2":
            page = response_page(github_page, 3, 190)
        else:
            initial_calls += 1
            page = response_page(
                github_page,
                1,
                9,
                cursor="p1",
                more=True,
                title="Old" if initial_calls == 1 else "Updated while paging",
            )
        return httpx.Response(200, json=page)

    context["router"].post("https://api.github.com/graphql").mock(side_effect=respond)
    job, created = await queued(context)
    assert created
    assert await sync_repo(context, "a/b", "backfill", str(job.id)) == "succeeded"
    repo, jobs = await load_state(context)
    assert repo.covered_since == NOW - timedelta(days=180)
    assert repo.last_open_sweep_at == repo.last_synced_at == NOW
    assert repo.last_sync_status == "ok" and repo.data_version >= 4
    assert jobs[0].status == "succeeded"
    assert any(v["states"] == ["OPEN"] for v in requests)
    assert [v["cursor"] for v in requests].count("p1") == 1
    async with context["session_factory"]() as session:
        records = (await session.scalars(select(PullRequest).order_by(PullRequest.number))).all()
        assert len(records) == 4 and records[0].title == "Updated while paging"
        assert await session.scalar(select(func.count()).select_from(PrEvent)) == 4
    previous_version = repo.data_version
    assert await sync_repo(context, "a/b", "incremental", str(job.id)) == "succeeded"
    repo, _ = await load_state(context)
    assert repo.data_version == previous_version


async def test_changed_content_replaces_events_and_files(context, github_page):
    context["router"].post("https://api.github.com/graphql").respond(200, json=github_page)
    job, _ = await queued(context)
    assert await sync_repo(context, "a/b", "backfill", str(job.id)) == "succeeded"
    before, _ = await load_state(context)
    node = github_page["data"]["repository"]["pullRequests"]["nodes"][0]
    node["files"]["nodes"] = [{"path": "src/B/new.cs"}]
    node["timelineItems"]["nodes"] = []
    context["router"].post("https://api.github.com/graphql").respond(200, json=github_page)
    assert await sync_repo(context, "a/b", "incremental", str(job.id)) == "succeeded"
    after, _ = await load_state(context)
    assert after.data_version == before.data_version + 1
    async with context["session_factory"]() as session:
        assert await session.scalar(select(func.count()).select_from(PullRequest)) == 1
        assert await session.scalar(select(func.count()).select_from(PrEvent)) == 0
        assert (await session.scalars(select(PrFile.path))).all() == ["src/B/new.cs"]


async def test_enqueue_deduplicates_without_extra_ledger_rows(context):
    first, created = await queued(context)
    second, again = await queued(context, "manual")
    assert created and not again and first.id == second.id
    async with context["session_factory"]() as session:
        assert await session.scalar(select(func.count()).select_from(SyncJob)) == 1


async def test_repository_lock_records_skipped_job(context):
    job, _ = await queued(context)
    await context["redis"].set(sync_lock_key("a/b"), "another-job", ex=60)
    assert await sync_repo(context, "a/b", "manual", str(job.id)) == "skipped_locked"
    _, jobs = await load_state(context)
    assert jobs[0].status == "failed" and jobs[0].error.startswith("skipped:")
    assert await context["redis"].get(sync_lock_key("a/b")) == b"another-job"


async def test_auth_failure_redacts_token(context):
    context["router"].post("https://api.github.com/graphql").respond(401, text="private body")
    job, _ = await queued(context)
    assert await sync_repo(context, "a/b", "manual", str(job.id)) == "failed"
    repo, jobs = await load_state(context)
    assert repo.last_sync_status == "auth_error"
    assert "private" not in jobs[0].error
    assert context["settings"].github_token.get_secret_value() not in jobs[0].error
    assert await context["redis"].get(sync_lock_key("a/b")) is None


async def test_missing_token_keeps_worker_usable(context):
    context["settings"] = context["settings"].model_copy(update={"github_token": None})
    await reconcile_tracked_repos(context)
    await incremental_sync_all(context)
    repo, jobs = await load_state(context)
    assert repo.last_sync_status == "missing_token" and not jobs
    job, _ = await queued(context, "manual")
    assert await sync_repo(context, "a/b", "manual", str(job.id)) == "missing_token"


async def test_incremental_runs_before_resumed_backfill(context, github_page):
    import orjson

    job, _ = await queued(context)
    async with context["session_factory"]() as session, session.begin():
        await session.execute(
            update(Repository).values(
                covered_since=NOW - timedelta(days=7),
                backfill_cursor="resume",
                backfill_target_days=30,
                sync_watermark=NOW - timedelta(hours=2),
                last_open_sweep_at=NOW,
            )
        )
    seen = []

    def respond(request):
        variables = orjson.loads(request.content)["variables"]
        seen.append(variables["cursor"])
        return httpx.Response(200, json=response_page(github_page, 1, 190))

    context["router"].post("https://api.github.com/graphql").mock(side_effect=respond)
    assert await sync_repo(context, "a/b", "backfill", str(job.id)) == "succeeded"
    assert seen[:2] == [None, "resume"]
    repo, _ = await load_state(context)
    assert repo.covered_since == NOW - timedelta(days=180)

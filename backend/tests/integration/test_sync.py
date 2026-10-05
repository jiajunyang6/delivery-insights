from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from insights.db.models import PrEvent, PrFact, PrFile, PrInterval, PullRequest, Repository, SyncJob
from insights.redis import sync_lock_key
from insights.sync.derive import current_key, derivation_complete
from insights.sync.jobs import SyncRun, incremental_sync_all, reconcile_tracked_repos, sync_repo
from insights.sync.queue import enqueue_sync

pytestmark = pytest.mark.integration
NOW = datetime(2026, 3, 2, tzinfo=UTC)


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
    assert repo.covered_since == NOW - timedelta(days=context["settings"].backfill_days)
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


async def test_failed_prefetched_page_keeps_the_previous_watermark(context, github_page):
    route = context["router"].post("https://api.github.com/graphql")
    route.respond(200, json=github_page)
    job, _ = await queued(context)
    assert await sync_repo(context, "a/b", "backfill", str(job.id)) == "succeeded"
    repo, _ = await load_state(context)
    previous_watermark = repo.sync_watermark

    # A new PR is committed, but the prefetched next page fails before checkpointing.
    route.mock(
        side_effect=[
            httpx.Response(200, json=response_page(github_page, 2, 0, "next", True)),
            httpx.Response(401),
        ]
    )
    assert await sync_repo(context, "a/b", "incremental", str(job.id)) == "failed"
    repo, _ = await load_state(context)
    assert repo.sync_watermark == previous_watermark
    route.respond(200, json=response_page(github_page, 2, 0))
    assert await sync_repo(context, "a/b", "incremental", str(job.id)) == "succeeded"


async def test_failed_page_write_does_not_advance_backfill_cursor(
    context, github_page, monkeypatch
):
    import insights.sync.jobs as jobs

    job, _ = await queued(context)
    repo, _ = await load_state(context)
    run = SyncRun(context, repo, str(job.id))
    context["router"].post("https://api.github.com/graphql").respond(
        200, json=response_page(github_page, 1, 0, "next", True)
    )
    original = jobs.save_page

    async def fail_after_insert(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("rollback")

    monkeypatch.setattr(jobs, "save_page", fail_after_insert)
    with pytest.raises(RuntimeError, match="rollback"):
        await run.backfill()
    repo, _ = await load_state(context)
    assert repo.backfill_cursor is None
    assert repo.covered_since is None and run.stats["pages"] == 0
    async with context["session_factory"]() as session:
        assert await session.scalar(select(func.count()).select_from(PullRequest)) == 0


async def test_nul_characters_are_stripped_and_watermark_advances(context, github_page):
    page = response_page(github_page, 1, 1)
    page["data"]["repository"]["pullRequests"]["nodes"][0]["title"] = "before\x00after"
    context["router"].post("https://api.github.com/graphql").respond(200, json=page)
    job, _ = await queued(context)
    assert await sync_repo(context, "a/b", "backfill", str(job.id)) == "succeeded"
    repo, jobs = await load_state(context)
    assert repo.sync_watermark == NOW - timedelta(days=1)
    assert jobs[0].stats["skipped_prs"] == 0
    async with context["session_factory"]() as session:
        assert await session.scalar(select(PullRequest.title)) == "beforeafter"


async def test_malformed_pr_is_skipped_while_other_prs_and_watermark_are_saved(
    context, github_page
):
    first = response_page(github_page, 1, 2)
    connection = first["data"]["repository"]["pullRequests"]
    good = response_page(github_page, 2, 3)["data"]["repository"]["pullRequests"]["nodes"][0]
    bad = response_page(github_page, 3, 1)["data"]["repository"]["pullRequests"]["nodes"][0]
    del bad["title"]
    connection["nodes"] = [bad, connection["nodes"][0], good]
    empty = deepcopy(github_page)
    empty["data"]["repository"]["pullRequests"]["nodes"] = []
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=first if calls == 1 else empty)

    context["router"].post("https://api.github.com/graphql").mock(side_effect=respond)
    job, _ = await queued(context)
    assert await sync_repo(context, "a/b", "backfill", str(job.id)) == "succeeded"
    repo, jobs = await load_state(context)
    assert jobs[0].stats["skipped_prs"] == 1
    assert jobs[0].stats["prs_fetched"] == 3
    assert repo.sync_watermark == NOW - timedelta(days=1)
    async with context["session_factory"]() as session:
        assert (
            await session.scalars(select(PullRequest.number).order_by(PullRequest.number))
        ).all() == [1, 2]
        assert await derivation_complete(session, repo.id, current_key(context["settings"]))


async def test_timeline_violation_is_counted_and_facts_are_still_saved(
    context, github_page, monkeypatch
):
    import insights.sync.derive as module

    original = module.build_timeline

    def broken_timeline(*args):
        result = original(*args)
        intervals = list(result.intervals)
        index = next(i for i, interval in enumerate(intervals) if interval.state != "coding")
        interval = intervals[index]
        # Leave a real coverage gap while retaining intervals valid for storage.
        intervals[index] = replace(interval, start_at=interval.start_at + timedelta(seconds=1))
        broken = replace(result, intervals=tuple(intervals))
        assert module.check_invariants(broken, args[0])
        return broken

    monkeypatch.setattr(module, "build_timeline", broken_timeline)
    node = github_page["data"]["repository"]["pullRequests"]["nodes"][0]
    node.update(state="MERGED", mergedAt="2026-01-05T00:00:00Z", closedAt="2026-01-05T00:00:00Z")
    context["router"].post("https://api.github.com/graphql").respond(200, json=github_page)
    job, _ = await queued(context)
    assert await sync_repo(context, "a/b", "backfill", str(job.id)) == "succeeded"
    repo, jobs = await load_state(context)
    assert jobs[0].stats["invariant_violations"] == 1
    assert repo.derived_key == current_key(context["settings"])
    async with context["session_factory"]() as session:
        assert await session.scalar(select(func.count()).select_from(PrFact)) == 1
        assert await session.scalar(select(func.count()).select_from(PrInterval)) > 0
        assert await derivation_complete(session, repo.id, current_key(context["settings"]))


async def test_enqueue_deduplicates_without_extra_ledger_rows(context):
    first, created = await queued(context)
    second, again = await queued(context, "incremental")
    assert created and not again and first.id == second.id
    async with context["session_factory"]() as session:
        assert await session.scalar(select(func.count()).select_from(SyncJob)) == 1


async def test_repository_lock_records_skipped_job(context):
    job, _ = await queued(context)
    await context["redis"].set(sync_lock_key("a/b"), "another-job", ex=60)
    assert await sync_repo(context, "a/b", "incremental", str(job.id)) == "skipped_locked"
    _, jobs = await load_state(context)
    assert jobs[0].status == "failed" and jobs[0].error.startswith("skipped:")
    assert await context["redis"].get(sync_lock_key("a/b")) == b"another-job"


async def test_auth_failure_redacts_token(context):
    context["router"].post("https://api.github.com/graphql").respond(401, text="private body")
    job, _ = await queued(context)
    assert await sync_repo(context, "a/b", "incremental", str(job.id)) == "failed"
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
    job, _ = await queued(context, "incremental")
    assert await sync_repo(context, "a/b", "incremental", str(job.id)) == "missing_token"


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
    assert repo.covered_since == NOW - timedelta(days=context["settings"].backfill_days)


async def test_reduced_backfill_target_preserves_coverage_without_resuming_history(
    context, github_page
):
    import orjson

    context["settings"] = context["settings"].model_copy(update={"backfill_days": 30})
    job, _ = await queued(context)
    covered_since = NOW - timedelta(days=30)
    async with context["session_factory"]() as session, session.begin():
        await session.execute(
            update(Repository).values(
                covered_since=covered_since,
                backfill_cursor="unfinished-180-day-cursor",
                backfill_target_days=180,
                sync_watermark=NOW - timedelta(hours=2),
                last_open_sweep_at=NOW,
            )
        )
    seen = []

    def respond(request):
        variables = orjson.loads(request.content)["variables"]
        seen.append(variables["cursor"])
        return httpx.Response(200, json=response_page(github_page, 1, 1))

    context["router"].post("https://api.github.com/graphql").mock(side_effect=respond)
    assert await sync_repo(context, "a/b", "backfill", str(job.id)) == "succeeded"
    repo, jobs = await load_state(context)
    assert seen and all(cursor is None for cursor in seen)
    assert repo.covered_since == covered_since
    assert repo.backfill_cursor == "unfinished-180-day-cursor"
    assert repo.backfill_target_days == 30 and repo.last_sync_status == "ok"
    assert next(j for j in jobs if j.id == job.id).status == "succeeded"

import asyncio
from contextlib import aclosing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from insights.config import Settings
from insights.domain import PageResult, RepositoryInfo
from insights.sources.github.client import GitHubError
from insights.sync.jobs import SyncRun

NOW = datetime(2026, 3, 2, tzinfo=UTC)


def page(cursor=None, more=False, days=0):
    updated = NOW - timedelta(days=days)
    return PageResult(RepositoryInfo("a/b", "main"), (), cursor, more, updated, updated, 1)


def run():
    return SyncRun(
        {"settings": Settings(), "adapter": SimpleNamespace(), "now": lambda: NOW},
        SimpleNamespace(owner="a", name="b"),
        "job",
    )


async def test_download_overlaps_write_and_only_one_page_is_prefetched():
    sync = run()
    second_started = asyncio.Event()
    release_write = asyncio.Event()
    first, second, third = page("one", True), page("two", True), page()
    fetched, stored = [], []

    async def fetch(cursor, **kwargs):
        fetched.append(cursor)
        if cursor == "one":
            second_started.set()
        return {None: first, "one": second, "two": third}[cursor]

    async def store(result, **kwargs):
        if result is first:
            await asyncio.wait_for(second_started.wait(), 2)
            assert fetched == [None, "one"]
            await release_write.wait()
        stored.append(result)

    sync.fetch, sync.store = fetch, store
    async with aclosing(sync.pages(backfill=True)) as pages:
        first_result = asyncio.create_task(anext(pages))
        await asyncio.wait_for(second_started.wait(), 2)
        assert stored == [] and fetched == [None, "one"]
        release_write.set()
        assert await first_result is first
        assert [result async for result in pages] == [second, third]
    assert stored == [first, second, third]


@pytest.mark.parametrize("failure", ["write", "consumer"])
async def test_abandoned_prefetch_is_cancelled(failure):
    sync = run()
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def fetch(cursor, **kwargs):
        if cursor is None:
            return page("next", True)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def store(result, **kwargs):
        await asyncio.wait_for(started.wait(), 2)
        if failure == "write":
            raise RuntimeError("write failed")

    sync.fetch, sync.store = fetch, store
    with pytest.raises(RuntimeError):
        async with aclosing(sync.pages()) as pages:
            await anext(pages)
            raise RuntimeError("consumer failed")
    assert cancelled.is_set()


@pytest.mark.parametrize("more,stop", [(False, False), (True, True)])
async def test_no_prefetch_after_last_page_or_cutoff(more, stop):
    sync = run()
    result = page("unused", more)
    sync.fetch = AsyncMock(return_value=result)
    sync.store = AsyncMock()
    assert [p async for p in sync.pages(stop=lambda _: stop)] == [result]
    sync.fetch.assert_awaited_once_with(None, open_only=False)
    sync.store.assert_awaited_once_with(result, backfill=False)


async def test_invalid_cursor_is_saved_before_error_without_refetch():
    sync = run()
    result = page("repeat", True)
    sync.fetch = AsyncMock(return_value=result)
    sync.store = AsyncMock()
    with pytest.raises(GitHubError, match="backfill_cursor_did_not_advance"):
        async with aclosing(
            sync.pages("repeat", cursor_error="backfill_cursor_did_not_advance")
        ) as pages:
            assert await anext(pages) is result
            await anext(pages)
    sync.fetch.assert_awaited_once_with("repeat", open_only=False)
    sync.store.assert_awaited_once()


async def test_prefetch_failure_does_not_publish_incremental_watermark():
    sync = run()
    sync.repo.sync_watermark = NOW - timedelta(hours=1)
    sync.fetch = AsyncMock(side_effect=[page("next", True), GitHubError("next failed")])
    sync.store = AsyncMock()
    with pytest.raises(GitHubError, match="next failed"):
        await sync.incremental(NOW - timedelta(hours=1))
    sync.store.assert_awaited_once()
    assert sync.repo.sync_watermark == NOW - timedelta(hours=1)

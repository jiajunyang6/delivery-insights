"""API against Postgres and Redis: caching, ETags, pending and 503s."""

from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import orjson
import pytest
from pydantic import SecretStr
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select, update
from tests.factories import at
from tests.integration.test_derive import seed
from tests.integration.test_sync import NOW

from insights.analytics import ANALYTICS_VERSION
from insights.api.deps import get_now
from insights.api.schemas import Insight, Pending, RepoList
from insights.db.models import Repository, Snapshot, SyncJob
from insights.main import create_app
from insights.redis import llm_error_key, snapshot_key
from insights.sync.derive import current_key
from insights.sync.queue import enqueue_sync

pytestmark = pytest.mark.integration
QUERY = "repo=a/b&from=2026-01-01&to=2026-01-01"
DELIVERY = "/v1/insights/delivery?" + QUERY


@pytest.fixture
async def api(context):
    await seed(context, 35)
    async with context["session_factory"]() as session, session.begin():
        await session.execute(
            update(Repository).values(
                covered_since=at(-1000),
                last_synced_at=NOW,
                last_open_sweep_at=NOW,
                last_sync_status="ok",
                derived_key=current_key(context["settings"]),
            )
        )
    app = create_app(context["settings"])
    app.state.session_factory = context["session_factory"]
    app.state.redis = context["redis"]
    clock = {"now": NOW}
    app.dependency_overrides[get_now] = lambda: clock["now"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, app, clock, context


async def test_snapshot_caches_etags_and_schema(api, monkeypatch):
    client, _, _, ctx = api
    first = await client.get(DELIVERY, headers={"X-Request-ID": "api-check"})
    assert first.status_code == 200, first.text
    Insight.model_validate(first.json())
    assert set(first.json()) == {
        "snapshot_id",
        "repo",
        "period",
        "as_of",
        "comparison_available",
        "insight",
        "time_ledger",
        "links",
    }
    assert first.headers["cache-control"] == "private, no-cache"
    assert first.headers["x-request-id"] == "api-check"
    assert first.headers["x-snapshot-id"] == first.json()["snapshot_id"]
    second = await client.get(DELIVERY)
    assert second.content == first.content
    monkeypatch.setattr(ctx["redis"], "hgetall", AsyncMock(side_effect=AssertionError("body read")))
    cached = await client.get(
        DELIVERY, headers={"If-None-Match": f'"unrelated", W/{first.headers["etag"]}'}
    )
    assert cached.status_code == 304 and cached.content == b""
    async with ctx["session_factory"]() as session:
        assert await session.scalar(select(func.count()).select_from(Snapshot)) == 1


async def test_analytics_version_isolates_postgres_redis_and_http_cache(api, monkeypatch):
    client, _, _, ctx = api
    with monkeypatch.context() as legacy:
        for module in ("dataset", "snapshot"):
            legacy.setattr(f"insights.analytics.{module}.ANALYTICS_VERSION", "1.4.0")
        legacy.setattr("insights.snapshots.service.ANALYTICS_VERSION", "1.4.0")
        old = await client.get(DELIVERY)
    assert old.status_code == 200
    fresh = await client.get(DELIVERY, headers={"If-None-Match": old.headers["etag"]})
    assert fresh.status_code == 200
    old_sid, new_sid = old.json()["snapshot_id"], fresh.json()["snapshot_id"]
    assert new_sid != old_sid
    async with ctx["session_factory"]() as session:
        assert (await session.get(Snapshot, new_sid)).analytics_version == ANALYTICS_VERSION
    assert fresh.headers["etag"] != old.headers["etag"]
    for sid in (old_sid, new_sid):
        assert await ctx["redis"].exists(snapshot_key(sid))
    async with ctx["session_factory"]() as session:
        assert await session.scalar(select(func.count()).select_from(Snapshot)) == 2
    cached = await client.get(DELIVERY, headers={"If-None-Match": fresh.headers["etag"]})
    assert cached.status_code == 304 and not cached.content


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ({"covered_since": None}, "never_synced"),
        ({"covered_since": at(1)}, "backfill"),
        ({"last_open_sweep_at": None}, "open_sweep"),
        ({"derived_key": "old"}, "rederive"),
        ({"last_synced_at": at(0)}, "stale"),
    ],
)
async def test_all_pending_reasons_and_no_get_enqueue(api, values, reason):
    client, _, _, ctx = api
    async with ctx["session_factory"]() as session:
        await session.execute(update(Repository).values(**values))
        await session.commit()
        job, _ = await enqueue_sync(
            ctx["redis"],
            session,
            "a/b",
            "rederive" if reason == "rederive" else "incremental",
            now=NOW,
        )
    result = await client.get(DELIVERY)
    assert result.status_code == 202, result.text
    Pending.model_validate(result.json())
    assert result.json()["repos"][0]["reason"] == reason
    assert result.json()["repos"][0]["job"] == {"status": job.status, "phase": job.phase}
    assert result.headers["retry-after"] == "30"
    async with ctx["session_factory"]() as session:
        assert await session.scalar(select(func.count()).select_from(SyncJob)) == 1


@pytest.mark.parametrize(
    ("values", "status"),
    [
        ({"last_sync_status": "missing_token", "covered_since": None}, 503),
        ({"last_sync_status": "auth_error", "last_synced_at": at(0)}, 503),
        ({"last_sync_status": "missing_token", "derived_key": "old"}, 202),
    ],
)
async def test_unavailable_vs_local_rederive(api, values, status):
    client, _, _, ctx = api
    async with ctx["session_factory"]() as session, session.begin():
        await session.execute(update(Repository).values(**values))
    response = await client.get(DELIVERY)
    assert response.status_code == status
    if status == 503:
        assert response.json()["type"] == "/problems/data-unavailable"


async def test_sync_after_configuration_fix_reports_progress_not_unavailable(api):
    client, _, _, ctx = api
    async with ctx["session_factory"]() as session:
        await session.execute(
            update(Repository).values(last_sync_status="missing_token", covered_since=None)
        )
        await session.commit()
        job, _ = await enqueue_sync(ctx["redis"], session, "a/b", "incremental", now=NOW)
    result = await client.get(DELIVERY)
    assert result.status_code == 202, result.text
    assert result.json()["repos"][0]["job"]["status"] == job.status
    problems = (await client.get("/v1/repos")).json()["setup"]["github"]["problems"]
    assert problems == [{"repo": "a/b", "status": "missing_token", "syncing": True}]


async def test_partial_watermark(api):
    client, _, _, ctx = api
    async with ctx["session_factory"]() as session, session.begin():
        await session.execute(update(Repository).values(last_synced_at=at(6)))
    response = await client.get(DELIVERY)
    assert response.status_code == 200
    assert response.json()["as_of"] == "2026-01-01T06:00:00Z"
    assert not response.json()["period"]["complete"]
    assert response.json()["insight"]["merged_prs"]["value"] == 0


async def test_expired_snapshot_is_recomputed_with_the_same_id(api):
    client, _, clock, ctx = api
    first = await client.get(DELIVERY)
    sid = first.json()["snapshot_id"]
    clock["now"] += timedelta(days=8)
    assert await ctx["redis"].exists(snapshot_key(sid))
    renewed = await client.get(DELIVERY)
    assert renewed.status_code == 200 and renewed.content == first.content
    async with ctx["session_factory"]() as session:
        assert (await session.get(Snapshot, sid)).created_at == clock["now"]


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/v1/insights/delivery?repo=x/y", 403),
        ("/v1/insights/delivery?repo=../../secret", 422),
        ("/v1/insights/delivery?repo=a/b&repo=c/d", 422),
        ("/v1/snapshots/nope/narrative", 422),
        ("/v1/snapshots/s_0000000000000000/narrative", 404),
    ],
)
async def test_errors_request_ids_and_cors(api, path, code):
    client, _, _, _ = api
    response = await client.get(
        path, headers={"Origin": "http://localhost:5173", "X-Request-ID": "x" * 100}
    )
    assert response.status_code == code
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert len(response.headers["x-request-id"]) == 32
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "../../secret" not in response.json()["detail"]


async def test_repos_list_tracked_repositories(api):
    client, _, _, ctx = api
    ctx["settings"].tracked_repos = "a/b,c/d"
    repos = await client.get("/v1/repos")
    items = RepoList.model_validate(repos.json()).items
    assert [item.model_dump() for item in items] == [
        {
            "repo": "a/b",
            "last_sync_status": "ok",
            "last_sync_error": None,
            "last_synced_at": NOW,
            "syncing": False,
        },
        {
            "repo": "c/d",
            "last_sync_status": "never",
            "last_sync_error": None,
            "last_synced_at": None,
            "syncing": False,
        },
    ]


async def test_repos_report_failed_sync_code_and_retry(api):
    client, _, _, ctx = api
    async with ctx["session_factory"]() as session, session.begin():
        await session.execute(
            update(Repository).values(
                last_sync_status="failed", last_sync_error="GitHubTransientError: invalid_json"
            )
        )
    item = (await client.get("/v1/repos")).json()["items"][0]
    assert item["last_sync_status"] == "failed" and item["syncing"] is False
    assert item["last_sync_error"] == "GitHubTransientError: invalid_json"
    async with ctx["session_factory"]() as session:
        await enqueue_sync(ctx["redis"], session, "a/b", "incremental", now=NOW)
    assert (await client.get("/v1/repos")).json()["items"][0]["syncing"] is True


async def test_rate_limit_fail_open_and_redis_cache_fallback(api, monkeypatch):
    client, _, _, ctx = api
    ctx["settings"].rate_limit_per_minute = 3
    for _ in range(3):
        assert (await client.get("/v1/repos")).status_code == 200
    limited = await client.get("/v1/repos")
    assert limited.status_code == 429 and limited.headers["x-ratelimit-remaining"] == "0"
    assert (await client.get("/healthz")).status_code == 200
    monkeypatch.setattr(
        ctx["redis"], "incr", AsyncMock(side_effect=RedisConnectionError("private"))
    )
    monkeypatch.setattr(
        ctx["redis"], "hgetall", AsyncMock(side_effect=RedisConnectionError("private"))
    )
    result = await client.get(DELIVERY)
    assert result.status_code == 200
    assert "private" not in result.text
    denied_origin = await client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in denied_origin.headers


async def test_complete_openapi_models(api):
    _, app, _, _ = api
    paths = app.openapi()["paths"]
    assert set(paths) == {
        "/v1/insights/delivery",
        "/v1/snapshots/{snapshot_id}/narrative",
        "/v1/repos",
        "/healthz",
        "/readyz",
    }
    for item in paths.values():
        assert set(item) == {"get"}
        assert item["get"]["responses"]["200"]["content"]["application/json"]["schema"]


async def test_repos_report_setup_problems_without_secret_values(api):
    client, _, _, ctx = api
    ctx["settings"].github_token = SecretStr("github-secret-value")
    ctx["settings"].aws_bearer_token_bedrock = None
    ctx["settings"].tracked_repos = "a/b,c/d"
    setup = (await client.get("/v1/repos")).json()["setup"]
    assert setup["github"] == {"token_configured": True, "problems": []}
    assert setup["llm"]["key_configured"] is False and setup["llm"]["last_error"] is None
    assert setup["llm"]["region"] == ctx["settings"].aws_region
    async with ctx["session_factory"]() as session, session.begin():
        await session.execute(update(Repository).values(last_sync_status="auth_error"))
    ctx["settings"].github_token = None
    ctx["settings"].aws_bearer_token_bedrock = SecretStr("bedrock-secret-value")
    settings = ctx["settings"]
    await ctx["redis"].set(
        llm_error_key(),
        orjson.dumps(
            {
                "code": "AccessDeniedException",
                "at": "2026-01-02T03:04:05Z",
                "model_id": settings.bedrock_model_id,
                "region": settings.aws_region,
            }
        ),
    )
    response = await client.get("/v1/repos")
    RepoList.model_validate(response.json())
    setup = response.json()["setup"]
    assert setup["github"] == {
        "token_configured": False,
        "problems": [{"repo": "a/b", "status": "auth_error", "syncing": False}],
    }
    assert setup["llm"]["key_configured"] is True
    assert setup["llm"]["last_error"] == "AccessDeniedException"
    assert setup["llm"]["last_error_at"] == "2026-01-02T03:04:05Z"
    assert "secret-value" not in response.text

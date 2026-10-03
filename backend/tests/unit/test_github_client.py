from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import httpx
import orjson
import pytest
from pydantic import SecretStr

from insights.config import Settings
from insights.domain import RepoRef
from insights.sources.github.adapter import GitHubAdapter
from insights.sources.github.client import (
    GitHubAuthError,
    GitHubClient,
    GitHubNotFoundError,
    GitHubQueryError,
    GitHubRateLimited,
    GitHubTransientError,
)

URL = "https://api.github.com/graphql"


@pytest.fixture
async def client(respx_mock):
    instance = GitHubClient(
        Settings(github_token=SecretStr("ghp_" + "x" * 36)), sleep=AsyncMock(), clock=lambda: 1000.0
    )
    instance.router = respx_mock
    yield instance
    await instance.aclose()


async def test_success(client, github_page):
    client.router.post(URL).respond(200, json=github_page)
    page = await GitHubAdapter(client).pull_requests_page(
        RepoRef("a", "b"), cursor=None, page_size=25
    )
    assert len(page.prs) == 1 and page.graphql_cost == 1
    assert client.rate_limit_remaining == 4800
    assert page.repository.full_name == "a/b"


@pytest.mark.parametrize(
    ("status", "headers", "delays", "exception"),
    [
        (429, {"retry-after": "3"}, [3, 3, 3], GitHubRateLimited),
        (
            403,
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1010"},
            [15, 15, 15],
            GitHubRateLimited,
        ),
        (403, {}, [60, 120, 240], GitHubRateLimited),
        (502, {}, [2, 4, 8], GitHubTransientError),
        (503, {}, [2, 4, 8], GitHubTransientError),
        (504, {}, [2, 4, 8], GitHubTransientError),
    ],
)
async def test_backoff(client, status, headers, delays, exception):
    route = client.router.post(URL).respond(status, headers=headers)
    with pytest.raises(exception):
        await client.graphql("query", {})
    assert route.call_count == 4
    assert [call.args[0] for call in client.sleep.call_args_list] == delays


async def test_timeout(client):
    client.router.post(URL).mock(side_effect=httpx.ReadTimeout("do not expose"))
    with pytest.raises(GitHubTransientError, match="network_unavailable"):
        await client.graphql("query", {})
    assert [c.args[0] for c in client.sleep.call_args_list] == [2, 4, 8]


@pytest.mark.parametrize(
    ("response", "exception"),
    [
        (httpx.Response(401, text="sensitive"), GitHubAuthError),
        (httpx.Response(200, json={"errors": [{"type": "NOT_FOUND"}]}), GitHubNotFoundError),
        (httpx.Response(200, json={"errors": [{"message": "unknown field"}]}), GitHubQueryError),
    ],
)
async def test_permanent_error_is_sanitized(client, response, exception, caplog):
    route = client.router.post(URL).mock(return_value=response)
    with pytest.raises(exception) as error:
        await client.graphql("query", {})
    assert route.call_count == 1
    assert "sensitive" not in str(error.value)
    assert client.settings.github_token.get_secret_value() not in caplog.text + str(error.value)


async def test_wait_budget(client):
    client.router.post(URL).respond(429, headers={"retry-after": "901"})
    with pytest.raises(GitHubRateLimited, match="wait_budget"):
        await client.graphql("query", {})
    client.sleep.assert_not_awaited()


async def test_proactive_quota_wait(client, github_page):
    github_page["data"]["rateLimit"] = {
        "cost": 1,
        "remaining": 199,
        "resetAt": "1970-01-01T00:20:00Z",
    }
    client.router.post(URL).respond(200, json=github_page)
    await client.graphql("query", {})
    client.sleep.assert_awaited_once_with(205.0)


async def test_adaptive_page_size_and_cursor(client, github_page):
    route = client.router.post(URL).mock(
        side_effect=[
            httpx.Response(200, json={"errors": [{"message": "Something went wrong"}]})
            for _ in range(3)
        ]
        + [httpx.Response(200, json=github_page), httpx.Response(200, json=github_page)]
    )
    adapter = GitHubAdapter(client)
    await adapter.pull_requests_page(RepoRef("a", "b"), cursor="cursor", page_size=25)
    await adapter.pull_requests_page(RepoRef("a", "b"), cursor="next", page_size=25)
    variables = [orjson.loads(c.request.content)["variables"] for c in route.calls]
    assert [v["pageSize"] for v in variables] == [25, 12, 6, 5, 5]
    assert [v["cursor"] for v in variables[:4]] == ["cursor"] * 4


async def test_complete_timeline_before_normalize(client, github_page):
    node = github_page["data"]["repository"]["pullRequests"]["nodes"][0]
    node["timelineItems"]["pageInfo"] = {"hasNextPage": True, "endCursor": "timeline-next"}
    event = deepcopy(node["timelineItems"]["nodes"][0])
    event["id"] = "commit2"
    event["commit"]["oid"] = "newcommit"
    followup = {
        "data": {
            "rateLimit": github_page["data"]["rateLimit"],
            "node": {
                "timelineItems": {
                    "nodes": [event],
                    "pageInfo": {"hasNextPage": False, "endCursor": "end"},
                }
            },
        }
    }
    route = client.router.post(URL).mock(
        side_effect=[httpx.Response(200, json=github_page), httpx.Response(200, json=followup)]
    )
    page = await GitHubAdapter(client).pull_requests_page(
        RepoRef("a", "b"), cursor=None, page_size=25
    )
    assert len(page.prs[0].events) == 2 and page.graphql_cost == 2
    assert orjson.loads(route.calls[1].request.content)["variables"]["cursor"] == "timeline-next"


async def test_rest_etag_cache(client):
    body = {"workflow_runs": []}
    redis = MagicMock()
    redis.hgetall = AsyncMock(side_effect=[{}, {b"etag": b'"abc"', b"body": orjson.dumps(body)}])
    pipeline = MagicMock()
    pipeline.__aenter__ = AsyncMock(return_value=pipeline)
    pipeline.__aexit__ = AsyncMock(return_value=False)
    pipeline.execute = AsyncMock()
    redis.pipeline.return_value = pipeline
    client.redis = redis
    route = client.router.get("https://api.github.com/repos/a/b/actions/runs").mock(
        side_effect=[httpx.Response(200, json=body, headers={"etag": '"abc"'}), httpx.Response(304)]
    )
    first = await client.rest_get("/repos/a/b/actions/runs")
    second = await client.rest_get("/repos/a/b/actions/runs")
    assert first.body == second.body == body
    assert route.calls[1].request.headers["if-none-match"] == '"abc"'


@pytest.mark.parametrize("path", ["https://evil.example/a", "//evil.example/a", "relative"])
async def test_rest_rejects_absolute_urls(client, path):
    with pytest.raises(ValueError):
        await client.rest_get(path)


async def test_public_repo_token_retains_team_request_without_org_scope(client, github_page):
    node = github_page["data"]["repository"]["pullRequests"]["nodes"][0]
    node["timelineItems"]["nodes"].append(
        {
            "__typename": "ReviewRequestedEvent",
            "id": "team-request",
            "createdAt": "2026-01-01T12:00:00Z",
            "actor": {"__typename": "User", "login": "requester"},
            "requestedReviewer": {"__typename": "Team"},
        }
    )

    def public_repo_only(request):
        query = orjson.loads(request.content)["query"]
        if "slug" in query:
            return httpx.Response(200, json={"errors": [{"type": "INSUFFICIENT_SCOPES"}]})
        return httpx.Response(200, json=github_page)

    client.router.post(URL).mock(side_effect=public_repo_only)
    page = await GitHubAdapter(client).pull_requests_page(
        RepoRef("a", "b"), cursor=None, page_size=25
    )
    team = next(e for e in page.prs[0].events if e.payload.get("reviewer_type") == "Team")
    assert team.payload["reviewer"] is None
    assert team.occurred_at.isoformat() == "2026-01-01T12:00:00+00:00"

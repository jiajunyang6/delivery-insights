from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import orjson
import pytest
from pydantic import SecretStr

from insights.config import Settings
from insights.domain import RepoRef
from insights.sources.github.client import (
    GitHubAdapter,
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


@pytest.mark.parametrize("broken", ["missing_title", "bad_time", "bad_labels", "bad_author"])
async def test_one_malformed_pr_does_not_block_page(client, github_page, broken):
    connection = github_page["data"]["repository"]["pullRequests"]
    damaged = deepcopy(connection["nodes"][0])
    damaged["number"] = 2
    damaged["updatedAt"] = "2026-01-03T00:00:00Z"
    if broken == "missing_title":
        del damaged["title"]
    elif broken == "bad_time":
        damaged["createdAt"] = "invalid"
    elif broken == "bad_labels":
        damaged["labels"] = None
    else:
        damaged["author"] = ["unexpected shape"]
    connection["nodes"].insert(0, damaged)
    client.router.post(URL).respond(200, json=github_page)
    page = await GitHubAdapter(client).pull_requests_page(
        RepoRef("a", "b"), cursor=None, page_size=25
    )
    assert [pr.number for pr in page.prs] == [1]
    assert page.skipped_prs == 1
    assert page.oldest_updated_at.isoformat() == "2026-01-02T00:00:00+00:00"
    assert page.newest_updated_at.isoformat() == "2026-01-03T00:00:00+00:00"


async def test_all_skipped_prs_retain_pagination_bounds(client, github_page):
    connection = github_page["data"]["repository"]["pullRequests"]
    del connection["nodes"][0]["title"]
    connection["pageInfo"] = {"hasNextPage": True, "endCursor": "next"}
    client.router.post(URL).respond(200, json=github_page)
    page = await GitHubAdapter(client).pull_requests_page(
        RepoRef("a", "b"), cursor=None, page_size=25
    )
    assert not page.prs and page.skipped_prs == 1
    assert page.has_next_page and page.end_cursor == "next"
    assert page.oldest_updated_at == page.newest_updated_at
    assert page.newest_updated_at.isoformat() == "2026-01-02T00:00:00+00:00"


async def test_timeline_network_failure_is_not_treated_as_a_bad_pr(client, github_page):
    node = github_page["data"]["repository"]["pullRequests"]["nodes"][0]
    node["timelineItems"]["pageInfo"] = {"hasNextPage": True, "endCursor": "next"}
    client.router.post(URL).mock(
        side_effect=[httpx.Response(200, json=github_page), httpx.Response(401)]
    )
    with pytest.raises(GitHubAuthError):
        await GitHubAdapter(client).pull_requests_page(RepoRef("a", "b"), cursor=None, page_size=25)


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
        + [httpx.Response(200, json=github_page) for _ in range(3)]
    )
    adapter = GitHubAdapter(client)
    await adapter.pull_requests_page(RepoRef("a", "b"), cursor="cursor", page_size=25)
    await adapter.pull_requests_page(RepoRef("a", "b"), cursor="next", page_size=25)
    assert adapter.page_size == 25
    await adapter.pull_requests_page(RepoRef("a", "b"), cursor="restored", page_size=25)
    variables = [orjson.loads(c.request.content)["variables"] for c in route.calls]
    assert [v["pageSize"] for v in variables] == [25, 12, 6, 5, 5, 25]
    assert [v["cursor"] for v in variables[:4]] == ["cursor"] * 4


async def test_page_failure_resets_recovery_streak(client, github_page):
    adapter = GitHubAdapter(client)
    adapter.page_size = 5
    client.router.post(URL).respond(200, json=github_page)
    await adapter.pull_requests_page(RepoRef("a", "b"), cursor=None, page_size=25)
    client.router.post(URL).respond(401)
    with pytest.raises(GitHubAuthError):
        await adapter.pull_requests_page(RepoRef("a", "b"), cursor="bad", page_size=25)
    assert adapter.successful_pages == 0 and adapter.page_size == 5
    route = client.router.post(URL).respond(200, json=github_page)
    calls_before = len(route.calls)
    for cursor in ("first", "second", "third"):
        await adapter.pull_requests_page(RepoRef("a", "b"), cursor=cursor, page_size=25)
    assert [
        orjson.loads(call.request.content)["variables"]["pageSize"]
        for call in route.calls[calls_before:]
    ] == [5, 5, 25]


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


@pytest.mark.parametrize("typename, is_bot", [("Bot", True), ("User", False)])
async def test_large_timeline_keeps_source_id_and_actor_type(client, github_page, typename, is_bot):
    node = github_page["data"]["repository"]["pullRequests"]["nodes"][0]
    person = {"__typename": typename, "login": "ordinary-account"}
    node["author"] = person
    events = [
        {
            "__typename": "IssueComment",
            "id": f"comment-{number}",
            "createdAt": "2026-01-02T00:00:00Z",
            "author": person,
        }
        for number in range(205)
    ]
    node["timelineItems"] = {
        "nodes": events[:100],
        "pageInfo": {"hasNextPage": True, "endCursor": "after-100"},
    }
    responses = [httpx.Response(200, json=github_page)]
    for start, end, more in ((100, 200, True), (200, 205, False)):
        responses.append(
            httpx.Response(
                200,
                json={
                    "data": {
                        "rateLimit": github_page["data"]["rateLimit"],
                        "node": {
                            "timelineItems": {
                                "nodes": events[start:end],
                                "pageInfo": {"hasNextPage": more, "endCursor": f"after-{end}"},
                            }
                        },
                    }
                },
            )
        )
    route = client.router.post(URL).mock(side_effect=responses)
    page = await GitHubAdapter(client).pull_requests_page(
        RepoRef("a", "b"), cursor=None, page_size=25
    )
    assert page.skipped_prs == 0 and page.graphql_cost == 3
    pr = page.prs[0]
    assert len(pr.events) == len({event.dedup_key for event in pr.events}) == 205
    assert pr.author.is_bot is is_bot and all(event.actor.is_bot is is_bot for event in pr.events)
    requests = [orjson.loads(call.request.content) for call in route.calls]
    assert [request["variables"] for request in requests[1:]] == [
        {"id": node["id"], "cursor": "after-100"},
        {"id": node["id"], "cursor": "after-200"},
    ]
    assert "nodes { id number" in " ".join(requests[0]["query"].split())
    assert "fragment ActorFields on Actor { __typename login }" in " ".join(
        requests[0]["query"].split()
    )


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

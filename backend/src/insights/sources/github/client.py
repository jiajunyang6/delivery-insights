"""GitHub GraphQL client (retries, rate-limit waits) and the SourceAdapter that pages PRs.

sources/github is the only package that calls GitHub.
"""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime
from time import time
from typing import Any, cast

import httpx
import structlog

from insights.config import REPO_RE, Settings, split_list
from insights.domain import (
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    GitHubQueryError,
    GitHubRateLimited,
    GitHubTransientError,
    PageResult,
    PullRequestRecord,
    RepoRef,
    RepositoryInfo,
)
from insights.sources.github.normalize import normalize_pr, parse_time, remove_nulls
from insights.sources.github.queries import PULL_REQUEST_TIMELINE, PULL_REQUESTS_PAGE

__all__ = [
    "GitHubAdapter",
    "GitHubAuthError",
    "GitHubClient",
    "GitHubError",
    "GitHubNotFoundError",
    "GitHubQueryError",
    "GitHubRateLimited",
    "GitHubTransientError",
]

logger = structlog.get_logger(__name__)


class GitHubClient:
    def __init__(
        self,
        settings: Settings,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time,
    ) -> None:
        """Create authenticated HTTP/quota handling with injectable time and sleep."""
        self.settings = settings
        self.sleep = sleep
        self.clock = clock
        self.lock = asyncio.Lock()
        self.rate_limit_remaining: int | None = None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "delivery-insights/1.0",
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token.get_secret_value()}"
        self.http = httpx.AsyncClient(
            headers=headers, timeout=httpx.Timeout(connect=10, read=60, write=30, pool=10)
        )

    async def aclose(self) -> None:
        """Close the owned HTTP client."""
        await self.http.aclose()

    async def _wait(self, delay: float, waited: float, reason: str) -> float:
        """Sleep and return accumulated wait in seconds; reject totals above 900."""
        delay = max(0.0, delay)
        if waited + delay > 900:
            raise GitHubRateLimited("wait_budget_exceeded")
        logger.info(
            "github_wait",
            seconds=delay,
            reason=reason,
            rate_limit_remaining=self.rate_limit_remaining,
        )
        await self.sleep(delay)
        return waited + delay

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send with up to four attempts, retrying network errors, 403/429 and 502-504.

        Retry-After or the primary-limit reset sets the delay when present. Other 4xx fail at
        once, and total sleep beyond 15 minutes raises GitHubRateLimited instead of stalling.
        """
        waited = 0.0
        for attempt in range(4):
            response: httpx.Response | None = None
            try:
                response = await self.http.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 3:
                    raise GitHubTransientError("network_unavailable") from None
                waited = await self._wait(2.0 ** (attempt + 1), waited, "network")
                continue
            if response.status_code == 401:
                raise GitHubAuthError("authentication_failed")
            if response.status_code == 404:
                raise GitHubNotFoundError("repository_not_found")
            if response.status_code in {403, 429, 502, 503, 504}:
                limited = response.status_code in {403, 429}
                if attempt == 3:
                    if limited:
                        raise GitHubRateLimited("rate_limit_retries_exhausted")
                    raise GitHubTransientError("upstream_unavailable")
                if response.headers.get("retry-after"):
                    delay = float(response.headers["retry-after"])
                    reason = "retry_after"
                elif response.headers.get("x-ratelimit-remaining") == "0":
                    delay = (
                        float(response.headers.get("x-ratelimit-reset", self.clock()))
                        + 5
                        - self.clock()
                    )
                    reason = "primary_rate_limit"
                else:
                    delay = (60.0 if limited else 2.0) * 2**attempt
                    reason = "secondary_rate_limit" if limited else "upstream"
                waited = await self._wait(delay, waited, reason)
                continue
            if response.status_code >= 400:
                raise GitHubQueryError(f"http_status_{response.status_code}")
            return response
        raise GitHubTransientError("retry_exhausted")

    async def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """POST a query and return its `data`, mapping GraphQL errors to GitHubError types.

        Requests are serialized, so a quota wait here pauses every caller of this client.
        Reject errors even with HTTP 200 and partial data: storing an incomplete event history
        as a successful page would make derived metrics and sync checkpoints unreliable.
        """
        async with self.lock:
            response = await self._request(
                "POST",
                self.settings.github_graphql_url,
                json={"query": query, "variables": variables},
            )
            try:
                body = response.json()
            except ValueError:
                raise GitHubQueryError("invalid_json") from None
            if not isinstance(body, dict):
                raise GitHubQueryError("invalid_graphql_response")
            errors = body.get("errors", [])
            if errors:
                if any(error.get("type") == "NOT_FOUND" for error in errors):
                    raise GitHubNotFoundError("repository_not_found")
                if any(
                    "timeout" in str(error.get("message", "")).lower()
                    or "something went wrong" in str(error.get("message", "")).lower()
                    for error in errors
                ):
                    raise GitHubTransientError("graphql_timeout")
                raise GitHubQueryError("graphql_query_failed")
            data = body.get("data")
            if not isinstance(data, dict):
                raise GitHubQueryError("missing_graphql_data")
            rate = data.get("rateLimit", {})
            if rate:
                self.rate_limit_remaining = int(rate["remaining"])
                logger.info(
                    "github_request",
                    graphql_cost=rate["cost"],
                    rate_limit_remaining=self.rate_limit_remaining,
                )
                # Below 200 points, wait for the reset rather than risk failing mid-sync.
                if self.rate_limit_remaining < 200:
                    reset = datetime.fromisoformat(rate["resetAt"]).timestamp()
                    await self._wait(reset + 5 - self.clock(), 0, "low_quota")
            return cast(dict[str, Any], data)


class GitHubAdapter:
    def __init__(self, client: GitHubClient) -> None:
        """Bind the shared GitHub client and initialize adaptive pagination state."""
        self.client = client
        self.reset()

    def reset(self) -> None:
        """Restore configured page size and clear the consecutive-success counter."""
        self.page_size = self.client.settings.graphql_page_size
        self.successful_pages = 0

    async def pull_requests_page(
        self, repo: RepoRef, *, cursor: str | None, page_size: int, open_only: bool = False
    ) -> PageResult:
        """Fetch one page of PRs, most recently updated first, with complete timelines.

        Transient errors halve the page size (floor 5) and retry; the configured size returns
        after two consecutive successful pages. PRs that fail normalization are counted in
        `skipped_prs` instead of failing the page.
        """
        try:
            page = await self._pull_requests_page(
                repo, cursor=cursor, page_size=page_size, open_only=open_only
            )
        except Exception:
            self.successful_pages = 0
            raise
        self.successful_pages = min(2, self.successful_pages + 1)
        if self.successful_pages == 2:
            self.page_size = self.client.settings.graphql_page_size
        return page

    async def _pull_requests_page(
        self, repo: RepoRef, *, cursor: str | None, page_size: int, open_only: bool = False
    ) -> PageResult:
        """Materialize a page, completing each PR's timeline before normalization.

        Page bounds retain available raw updatedAt values even when other fields are malformed,
        so normalization skips do not discard watermark progress. Upstream failures propagate;
        they must not be treated as a successfully completed page with missing history.
        """
        if not REPO_RE.fullmatch(repo.full_name):
            raise ValueError("Invalid repository")
        size = min(page_size, self.page_size)
        while True:
            try:
                data = await self.client.graphql(
                    PULL_REQUESTS_PAGE,
                    {
                        "owner": repo.owner,
                        "name": repo.name,
                        "pageSize": size,
                        "cursor": cursor,
                        "states": ["OPEN"] if open_only else None,
                    },
                )
                break
            except GitHubTransientError:
                self.successful_pages = 0
                if size <= 5:
                    raise
                size = max(5, size // 2)
                self.page_size = size
        repository = data.get("repository")
        if not repository:
            raise GitHubNotFoundError("repository_not_found")
        connection = repository["pullRequests"]
        cost = int(data["rateLimit"]["cost"])
        extra_bots = frozenset(x.lower() for x in split_list(self.client.settings.extra_bot_logins))
        prs: list[PullRequestRecord] = []
        updated_at: list[datetime] = []
        skipped = 0
        for node in connection["nodes"]:
            # Preserve page bounds even when a PR's other fields cannot be normalized.
            with suppress(KeyError, TypeError, ValueError, AttributeError):
                updated_at.append(parse_time(remove_nulls(node["updatedAt"])))
            try:
                # Timelines over 100 items continue by PR node id, which is why queries fetch `id`.
                timeline = node["timelineItems"]
                seen: set[str] = set()
                while timeline["pageInfo"]["hasNextPage"]:
                    next_cursor = timeline["pageInfo"]["endCursor"]
                    if not next_cursor or next_cursor in seen:
                        raise GitHubTransientError("timeline_cursor_did_not_advance")
                    seen.add(next_cursor)
                    extra = await self.client.graphql(
                        PULL_REQUEST_TIMELINE, {"id": node["id"], "cursor": next_cursor}
                    )
                    cost += int(extra["rateLimit"]["cost"])
                    if not extra.get("node"):
                        raise GitHubNotFoundError("pull_request_not_found")
                    page = extra["node"]["timelineItems"]
                    timeline["nodes"].extend(page["nodes"])
                    timeline["pageInfo"] = page["pageInfo"]
                prs.append(normalize_pr(node, extra_bots))
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                number = node.get("number") if isinstance(node, dict) else None
                logger.warning(
                    "pr_normalize_failed",
                    repo=repo.full_name,
                    number=number if isinstance(number, int) else None,
                    error=type(exc).__name__,
                )
                skipped += 1
        return PageResult(
            RepositoryInfo(
                remove_nulls(repository["nameWithOwner"]),
                remove_nulls((repository.get("defaultBranchRef") or {}).get("name", "")),
            ),
            tuple(prs),
            connection["pageInfo"]["endCursor"],
            connection["pageInfo"]["hasNextPage"],
            min(updated_at, default=None),
            max(updated_at, default=None),
            cost,
            skipped,
        )

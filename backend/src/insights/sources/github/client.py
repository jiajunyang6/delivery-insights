"""HTTP client for GitHub GraphQL and REST: retries, rate-limit waits and ETag caching."""

from insights.domain import (
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    GitHubQueryError,
    GitHubRateLimited,
    GitHubTransientError,
)

__all__ = [
    "GitHubAuthError",
    "GitHubClient",
    "GitHubError",
    "GitHubNotFoundError",
    "GitHubQueryError",
    "GitHubRateLimited",
    "GitHubTransientError",
    "RestResponse",
]
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from time import time
from typing import Any, cast

import httpx
import orjson
import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from insights.config import Settings
from insights.redis import github_etag_key

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RestResponse:
    body: Any


class GitHubClient:
    def __init__(
        self,
        settings: Settings,
        redis: Redis | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time,
    ) -> None:
        self.settings = settings
        self.redis = redis
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
        await self.http.aclose()

    async def _wait(self, delay: float, waited: float, reason: str) -> float:
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

    async def rest_get(
        self,
        path: str,
        params: Mapping[str, str | int] | None = None,
        *,
        accept: str | None = None,
    ) -> RestResponse:
        if not path.startswith("/") or path.startswith("//") or "://" in path or "\\" in path:
            raise ValueError("REST path must be a relative API path")
        url = str(httpx.URL(self.settings.github_api_url.rstrip("/") + path, params=params))
        key = github_etag_key(url)
        async with self.lock:
            cached: dict[bytes, bytes] = {}
            if self.redis is not None:
                try:
                    cached = await cast(Awaitable[dict[bytes, bytes]], self.redis.hgetall(key))
                except RedisError:
                    logger.warning("github_cache_unavailable")
            headers = {"Accept": accept} if accept else {}
            if b"etag" in cached and b"body" in cached:
                headers["If-None-Match"] = cached[b"etag"].decode()
            response = await self._request("GET", url, headers=headers)
            if response.status_code == 304:
                if b"body" not in cached:
                    raise GitHubQueryError("conditional_response_without_cache")
                return RestResponse(orjson.loads(cached[b"body"]))
            body: Any = response.text if accept and "raw" in accept else response.json()
            etag = response.headers.get("etag")
            if self.redis is not None and etag:
                try:
                    async with self.redis.pipeline(transaction=True) as pipeline:
                        pipeline.hset(key, mapping={"etag": etag, "body": orjson.dumps(body)})
                        pipeline.expire(key, 7 * 86400)
                        await pipeline.execute()
                except RedisError:
                    logger.warning("github_cache_unavailable")
            return RestResponse(body)

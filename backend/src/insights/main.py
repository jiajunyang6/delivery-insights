"""FastAPI application factory and the outermost request middleware.

Request handlers read Postgres and Redis, never GitHub.
"""

import asyncio
import math
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.exceptions import RedisError
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from insights import __version__
from insights.api.deps import get_now
from insights.api.errors import ProblemError, handle_problem, install_handlers, problem_response
from insights.api.routes import health, insights, repos
from insights.config import Settings, get_settings, split_list
from insights.db.models import create_database
from insights.logging import configure_logging
from insights.narrative.llm import BedrockClient
from insights.narrative.service import check_llm
from insights.redis import create_redis, rate_limit_key

logger = structlog.get_logger(__name__)
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestMiddleware:
    """Tag requests with an id, rate-limit /v1/ per client IP and minute, and log completion."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the downstream ASGI application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Attach request context/security headers and rate-limit HTTP /v1/ requests.

        Redis failure leaves reads available; uncaught errors become problems before headers start.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if REQUEST_ID_RE.fullmatch(incoming) else uuid4().hex
        request.state.request_id = request_id
        tokens = structlog.contextvars.bind_contextvars(request_id=request_id)
        started = perf_counter()
        status = 500
        response_started = False
        limit = request.app.state.settings.rate_limit_per_minute
        remaining = limit
        limited = request.url.path.startswith("/v1/")

        async def wrapped_send(message: Message) -> None:
            """Track response start/status and append request, cache and rate-limit headers."""
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                if not any(k.lower() == b"cache-control" for k, _ in message.get("headers", [])):
                    message.setdefault("headers", []).append((b"cache-control", b"no-store"))
                if limited:
                    message.setdefault("headers", []).extend(
                        [
                            (b"x-ratelimit-limit", str(limit).encode()),
                            (b"x-ratelimit-remaining", str(remaining).encode()),
                        ]
                    )
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                ]
            await send(message)

        try:
            if limited:
                # Honor a get_now override so tests that freeze time also control the window.
                clock = request.app.dependency_overrides.get(get_now, get_now)
                epoch = clock().timestamp()
                key = rate_limit_key(
                    request.client.host if request.client else "unknown", int(epoch // 60)
                )
                try:
                    redis = request.app.state.redis
                    count = await redis.incr(key)
                    if count == 1:
                        await redis.expire(key, 70)
                    remaining = max(0, limit - count)
                    if count > limit:
                        error = ProblemError(
                            429,
                            "rate-limited",
                            "Too many requests",
                            "The request rate limit has been exceeded.",
                            headers={"Retry-After": str(max(1, math.ceil(60 - epoch % 60)))},
                        )
                        await problem_response(request, error)(scope, receive, wrapped_send)
                        return
                # Fail open: a Redis outage should not take down reads served from Postgres.
                except (RedisError, OSError, TimeoutError) as exc:
                    logger.warning("rate_limit_unavailable", error_type=type(exc).__name__)
            await self.app(scope, receive, wrapped_send)
        except Exception as exc:
            # Once headers are sent a problem body can no longer replace the response.
            if response_started:
                raise
            response = await handle_problem(request, exc)
            await response(scope, receive, wrapped_send)
        finally:
            if request.url.path not in {"/healthz", "/readyz"}:
                logger.info(
                    "request_completed",
                    method=request.method,
                    path=request.url.path,
                    status=status,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                )
            structlog.contextvars.reset_contextvars(**tokens)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app; settings are injectable for tests and connections open in the lifespan."""
    configuration = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Initialize app clients/session factories and close them on shutdown."""
        configure_logging(configuration.log_level)
        engine, sessions = create_database(configuration)
        redis = create_redis(configuration)
        app.state.session_factory = sessions
        app.state.redis = redis
        app.state.llm = BedrockClient(configuration) if configuration.llm_enabled else None
        # Check the Bedrock settings in the background so the setup notice reflects them
        # right after a restart, even when every narrative is served from cache.
        llm_check = (
            asyncio.create_task(check_llm(app.state.llm, redis, configuration, datetime.now(UTC)))
            if app.state.llm is not None
            else None
        )
        try:
            yield
        finally:
            if llm_check is not None:
                llm_check.cancel()
                with suppress(asyncio.CancelledError):
                    await llm_check
            await redis.aclose()
            await engine.dispose()
            if app.state.llm is not None:
                app.state.llm.client.close()

    app = FastAPI(title="Delivery Insights API", version=__version__, lifespan=lifespan)
    app.state.settings = configuration
    install_handlers(app)
    app.include_router(health.router)
    for router in (insights.router, repos.router):
        app.include_router(router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=split_list(configuration.cors_origins),
        allow_methods=["GET"],
        allow_headers=["If-None-Match", "X-Request-ID"],
        expose_headers=["ETag", "Retry-After", "X-Request-ID", "X-Snapshot-Id"],
        allow_credentials=False,
    )
    # Added last so it is outermost: request ids, rate limits and headers cover CORS replies too.
    app.add_middleware(RequestMiddleware)
    return app


app = create_app()

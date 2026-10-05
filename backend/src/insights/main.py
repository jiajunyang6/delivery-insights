"""FastAPI application factory; request handlers read Postgres and Redis, never GitHub."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.exceptions import RedisError

from insights import __version__
from insights.api.errors import install_handlers
from insights.api.middleware import RequestMiddleware
from insights.api.routes import health, insights, repos, snapshots, sync_jobs
from insights.config import Settings, get_settings, split_list
from insights.db.engine import create_database
from insights.logging import configure_logging
from insights.narrative.llm import BedrockClient
from insights.narrative.service import check_llm
from insights.redis import connect_arq, create_redis


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app; settings are injectable for tests and connections open in the lifespan."""
    configuration = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Initialize app clients/session factories and close them on shutdown.

        Queue startup is best effort; manual-sync dependencies can reconnect lazily.
        """
        configure_logging(configuration.log_level)
        engine, sessions = create_database(configuration)
        redis = create_redis(configuration)
        app.state.session_factory = sessions
        app.state.redis = redis
        app.state.arq = None
        app.state.llm = BedrockClient(configuration) if configuration.llm_enabled else None
        # Check the Bedrock settings in the background so the setup notice reflects them
        # right after a restart, even when every narrative is served from cache.
        llm_check = (
            asyncio.create_task(check_llm(app.state.llm, redis, configuration, datetime.now(UTC)))
            if app.state.llm is not None
            else None
        )
        # The queue is only needed for manual syncs; deps.get_arq reconnects lazily.
        with suppress(RedisError, OSError, TimeoutError):
            app.state.arq = await connect_arq(configuration.redis_url)
        try:
            yield
        finally:
            if llm_check is not None:
                llm_check.cancel()
                with suppress(asyncio.CancelledError):
                    await llm_check
            if app.state.arq is not None:
                await app.state.arq.aclose()
            await redis.aclose()
            await engine.dispose()
            if app.state.llm is not None:
                app.state.llm.client.close()

    app = FastAPI(title="Delivery Insights API", version=__version__, lifespan=lifespan)
    app.state.settings = configuration
    install_handlers(app)
    app.include_router(health.router)
    for router in (insights.router, snapshots.router, repos.router, sync_jobs.router):
        app.include_router(router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=split_list(configuration.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["If-None-Match", "Content-Type", "X-Request-ID"],
        expose_headers=[
            "ETag",
            "Retry-After",
            "Location",
            "Content-Location",
            "X-Request-ID",
            "X-Snapshot-Id",
        ],
        allow_credentials=False,
    )
    # Added last so it is outermost: request ids, rate limits and headers cover CORS replies too.
    app.add_middleware(RequestMiddleware)
    return app


app = create_app()

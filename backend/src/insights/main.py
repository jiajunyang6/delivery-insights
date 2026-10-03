from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq.connections import RedisSettings, create_pool
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
from insights.redis import create_redis


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(configuration.log_level)
        engine, sessions = create_database(configuration)
        redis = create_redis(configuration)
        app.state.session_factory = sessions
        app.state.redis = redis
        app.state.arq = None
        try:
            redis_settings = RedisSettings.from_dsn(configuration.redis_url)
            redis_settings.conn_retries = 0
            redis_settings.conn_timeout = 2
            app.state.arq = await create_pool(redis_settings)
        except (RedisError, OSError, TimeoutError):
            pass
        try:
            yield
        finally:
            if app.state.arq is not None:
                await app.state.arq.aclose()
            await redis.aclose()
            await engine.dispose()

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
    app.add_middleware(RequestMiddleware)
    return app


app = create_app()

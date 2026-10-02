from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from insights.api.errors import install_handlers
from insights.api.middleware import RequestMiddleware
from insights.api.routes import health
from insights.config import Settings, get_settings, split_list
from insights.db.engine import create_database
from insights.logging import configure_logging
from insights.redis import create_redis


async def default_cache(request: Request, call_next: RequestResponseEndpoint) -> Response:
    response = await call_next(request)
    if "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(configuration.log_level)
        engine, sessions = create_database(configuration)
        redis = create_redis(configuration)
        app.state.session_factory = sessions
        app.state.redis = redis
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(title="Delivery Insights", version="1.0.0", lifespan=lifespan)
    app.state.settings = configuration
    install_handlers(app)
    app.include_router(health.router)
    app.add_middleware(BaseHTTPMiddleware, dispatch=default_cache)
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

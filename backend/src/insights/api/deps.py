"""Dependency providers backed by the clients the app lifespan stores on app.state."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, cast

from arq.connections import ArqRedis
from fastapi import Depends, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insights.api.errors import unavailable
from insights.config import Settings
from insights.redis import connect_arq
from insights.snapshots.service import SnapshotService


def get_settings(request: Request) -> Settings:
    """Return the application's configured settings for dependency injection."""
    return cast(Settings, request.app.state.settings)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped database session and close it when the request finishes."""
    factory = cast(async_sessionmaker[AsyncSession], request.app.state.session_factory)
    async with factory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    """Return the application's shared Redis client."""
    return cast(Redis, request.app.state.redis)


def get_now() -> datetime:
    """Return the current aware UTC time; dependency overrides can freeze it in tests."""
    return datetime.now(UTC)


def get_snapshot_service(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
    now: Annotated[datetime, Depends(get_now)],
) -> "SnapshotService":
    """Bind snapshot orchestration to shared dependencies and this request's observation time."""
    return SnapshotService(request.app.state.session_factory, redis, settings, now)


async def get_arq(request: Request) -> "ArqRedis":
    """Shared arq pool, connected on first use if startup could not reach Redis; 503 if down."""
    existing = getattr(request.app.state, "arq", None)
    if existing is not None:
        return cast(ArqRedis, existing)
    try:
        pool = await connect_arq(get_settings(request).redis_url)
    except (RedisError, OSError, TimeoutError) as exc:
        raise unavailable("The sync queue is unavailable.") from exc
    request.app.state.arq = pool
    return pool

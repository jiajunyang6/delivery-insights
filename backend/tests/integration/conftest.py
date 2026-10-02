import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from arq.connections import RedisSettings, create_pool
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from insights.config import Settings
from insights.db.models import Base
from insights.sources.github.adapter import GitHubAdapter
from insights.sources.github.client import GitHubClient


@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def redis_url():
    with RedisContainer("redis:7-alpine") as container:
        yield f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"


@pytest.fixture
def migrated_database(postgres_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")

    async def clear():
        engine = create_async_engine(postgres_url)
        async with engine.begin() as connection:
            for table in reversed(Base.metadata.sorted_tables):
                await connection.execute(table.delete())
        await engine.dispose()

    asyncio.run(clear())
    return postgres_url


@pytest.fixture
async def context(migrated_database, redis_url, respx_mock):
    settings = Settings(
        database_url=migrated_database,
        redis_url=redis_url,
        tracked_repos="a/b",
        github_token=SecretStr("ghp_" + "x" * 36),
    )
    engine = create_async_engine(migrated_database)
    redis = await create_pool(RedisSettings.from_dsn(redis_url))
    await redis.flushdb()
    client = GitHubClient(settings, redis, sleep=AsyncMock())
    ctx = {
        "settings": settings,
        "engine": engine,
        "session_factory": async_sessionmaker(engine, expire_on_commit=False),
        "redis": redis,
        "adapter": GitHubAdapter(client),
        "now": lambda: datetime(2026, 3, 2, tzinfo=UTC),
        "router": respx_mock,
    }
    yield ctx
    await client.aclose()
    await redis.aclose()
    await engine.dispose()

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from insights.api.deps import get_redis, get_session
from insights.api.errors import ProblemError

router = APIRouter()


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str


class Readiness(Health):
    checks: dict[str, str]


@router.get("/healthz", response_model=Health)
async def healthz() -> Health:
    return Health(status="ok")


@router.get("/readyz", response_model=Readiness)
async def readyz(
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Readiness:
    checks: dict[str, str] = {}
    try:
        async with asyncio.timeout(2):
            await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except (SQLAlchemyError, OSError, TimeoutError):
        checks["postgres"] = "error"
    try:
        async with asyncio.timeout(2):
            await redis.ping()
        checks["redis"] = "ok"
    except (RedisError, OSError, TimeoutError):
        checks["redis"] = "error"
    if "error" in checks.values():
        raise ProblemError(
            503,
            "dependency-unavailable",
            "Dependency unavailable",
            "One or more dependencies are unavailable.",
            extensions={"checks": checks},
        )
    return Readiness(status="ready", checks=checks)

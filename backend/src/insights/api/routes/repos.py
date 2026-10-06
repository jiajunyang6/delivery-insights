"""Tracked repository status, selectable date limits and configuration health."""

from contextlib import suppress
from datetime import datetime, timedelta
from typing import Annotated

import orjson
from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from insights.api.deps import get_now, get_redis, get_session, get_settings
from insights.api.schemas import (
    DateLimits,
    GithubProblem,
    GithubSetup,
    LlmSetup,
    RepoList,
    RepoStatus,
    SetupStatus,
)
from insights.config import MAX_PERIOD_DAYS, Settings
from insights.db.models import Repository, SyncJob
from insights.redis import llm_error_key

router = APIRouter(prefix="/v1/repos", tags=["Repositories"])

SETUP_SYNC_PROBLEMS = {"missing_token", "auth_error", "not_found"}


@router.get("", response_model=RepoList)
async def repositories(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
    now: Annotated[datetime, Depends(get_now)],
) -> RepoList:
    """Report configured repositories, allowed dates and configuration health."""
    configured = settings.tracked_repo_list
    repos = {
        r.full_name_lower: r
        for r in await session.scalars(
            select(Repository).where(
                Repository.full_name_lower.in_([r.lower() for r in configured])
            )
        )
    }
    # A queued or running sync may already use fixed settings, so its repository is reported
    # as syncing rather than with the status of its last finished sync.
    syncing = set(
        await session.scalars(
            select(SyncJob.repo_id).where(
                SyncJob.repo_id.in_([r.id for r in repos.values()]),
                SyncJob.kind.in_(["incremental", "backfill"]),
                SyncJob.status.in_(["queued", "running"]),
            )
        )
    )
    items = []
    problems = []
    for name in configured:
        repo = repos.get(name.lower())
        status = repo.last_sync_status if repo else "never"
        active = bool(repo and repo.id in syncing)
        items.append(
            RepoStatus(
                repo=name,
                last_sync_status=status,
                last_sync_error=repo.last_sync_error if repo else None,
                last_synced_at=repo.last_synced_at if repo else None,
                syncing=active,
            )
        )
        if status in SETUP_SYNC_PROBLEMS:
            problems.append(GithubProblem(repo=name, status=status, syncing=active))
    return RepoList(
        items=items,
        date_limits=DateLimits(
            earliest_from=now.date() - timedelta(days=settings.backfill_days),
            latest_to=now.date(),
            max_days=MAX_PERIOD_DAYS,
        ),
        setup=await setup_status(settings, redis, problems),
    )


async def setup_status(
    settings: Settings, redis: Redis, problems: list[GithubProblem]
) -> SetupStatus:
    """Summarize `.env` problems the user can fix; secrets are reported as present or absent."""
    last_error = None
    # Best effort: a Redis outage hides the last Bedrock error but must not fail this endpoint.
    with suppress(RedisError, OSError, TimeoutError, orjson.JSONDecodeError, TypeError):
        raw = await redis.get(llm_error_key())
        last_error = orjson.loads(raw) if raw else None
    # An error recorded before BEDROCK_MODEL_ID or AWS_REGION changed says nothing about the
    # current settings, so it is dropped until a call with them fails again.
    if last_error and (
        last_error.get("model_id") != settings.bedrock_model_id
        or last_error.get("region") != settings.aws_region
    ):
        last_error = None
    token = settings.github_token
    return SetupStatus(
        github=GithubSetup(
            token_configured=bool(token and token.get_secret_value()),
            problems=problems,
        ),
        llm=LlmSetup(
            key_configured=settings.llm_enabled,
            region=settings.aws_region,
            model_id=settings.bedrock_model_id,
            last_error=last_error["code"] if last_error else None,
            last_error_at=last_error["at"] if last_error else None,
        ),
    )

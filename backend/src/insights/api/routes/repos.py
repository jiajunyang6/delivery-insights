"""Tracked repository status, selectable date limits and manual sync requests."""

from contextlib import suppress
from datetime import datetime, timedelta
from typing import Annotated

import orjson
from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from insights.api.deps import get_arq, get_now, get_redis, get_session, get_settings
from insights.api.errors import ProblemError, unavailable
from insights.api.params import parse_repo_path
from insights.api.responses import job_response
from insights.api.schemas import (
    DateLimits,
    GithubProblem,
    GithubSetup,
    LlmSetup,
    RepoList,
    RepoStatus,
    SetupStatus,
    SyncJobResponse,
)
from insights.config import MAX_PERIOD_DAYS, Settings
from insights.db.models import Repository, SyncJob
from insights.redis import llm_error_key, sync_cooldown_key
from insights.sync.queue import enqueue_sync

router = APIRouter(prefix="/v1/repos", tags=["Repositories"])


@router.get("", response_model=RepoList)
async def repositories(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
    now: Annotated[datetime, Depends(get_now)],
) -> RepoList:
    """Report configured repositories, latest jobs, allowed dates and configuration health."""
    configured = settings.tracked_repo_list
    repos = {
        r.full_name_lower: r
        for r in await session.scalars(
            select(Repository).where(
                Repository.full_name_lower.in_([r.lower() for r in configured])
            )
        )
    }
    jobs = {
        j.repo_id: j
        for j in await session.scalars(
            select(SyncJob)
            .where(SyncJob.repo_id.in_([r.id for r in repos.values()]))
            .ext(distinct_on(SyncJob.repo_id))
            .order_by(SyncJob.repo_id, SyncJob.created_at.desc(), SyncJob.id.desc())
        )
    }
    items = []
    for name in configured:
        repo = repos.get(name.lower())
        job = jobs.get(repo.id) if repo else None
        items.append(
            RepoStatus(
                repo=name,
                default_branch=repo.default_branch if repo else None,
                covered_since=repo.covered_since if repo else None,
                backfill_target_days=repo.backfill_target_days if repo else None,
                backfill_complete=bool(
                    repo
                    and repo.covered_since
                    and repo.covered_since <= now - timedelta(days=settings.backfill_days)
                ),
                sync_watermark=repo.sync_watermark if repo else None,
                last_synced_at=repo.last_synced_at if repo else None,
                last_open_sweep_at=repo.last_open_sweep_at if repo else None,
                last_sync_status=repo.last_sync_status if repo else "never",
                last_sync_error=repo.last_sync_error if repo else None,
                data_version=repo.data_version if repo else 0,
                latest_job=job_response(job, name) if job else None,
            )
        )
    return RepoList(
        items=items,
        date_limits=DateLimits(
            earliest_from=now.date() - timedelta(days=settings.backfill_days),
            latest_to=now.date(),
            max_days=MAX_PERIOD_DAYS,
        ),
        setup=await setup_status(settings, redis, items),
    )


SETUP_SYNC_PROBLEMS = {"missing_token", "auth_error", "not_found"}


async def setup_status(settings: Settings, redis: Redis, items: list[RepoStatus]) -> SetupStatus:
    """Summarize `.env` problems the user can fix; secrets are reported as present or absent."""
    last_error = None
    # Best effort: a Redis outage hides the last Bedrock error but must not fail this endpoint.
    with suppress(RedisError, OSError, TimeoutError, orjson.JSONDecodeError, TypeError):
        raw = await redis.get(llm_error_key())
        last_error = orjson.loads(raw) if raw else None
    token = settings.github_token
    return SetupStatus(
        github=GithubSetup(
            token_configured=bool(token and token.get_secret_value()),
            problems=[
                GithubProblem(repo=item.repo, status=item.last_sync_status)
                for item in items
                if item.last_sync_status in SETUP_SYNC_PROBLEMS
            ],
        ),
        llm=LlmSetup(
            key_configured=settings.llm_enabled,
            region=settings.aws_region,
            model_id=settings.bedrock_model_id,
            last_error=last_error["code"] if last_error else None,
            last_error_at=last_error["at"] if last_error else None,
        ),
    )


def validated_repo_path(
    owner: str, name: str, settings: Annotated[Settings, Depends(get_settings)]
) -> str:
    """Resolve route owner/name to an allowed repository through the shared validator."""
    return parse_repo_path(owner, name, settings)


@router.post("/{owner}/{name}/sync", response_model=SyncJobResponse, status_code=202)
async def manual_sync(
    response: Response,
    repo: Annotated[str, Depends(validated_repo_path)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
    arq: Annotated[ArqRedis, Depends(get_arq)],
    now: Annotated[datetime, Depends(get_now)],
) -> SyncJobResponse:
    """Claim the repository cooldown, enqueue manual sync and return its status URL.

    An existing cooldown raises 429; unavailable queue transport raises a sanitized 503.
    """
    try:
        key = sync_cooldown_key(repo)
        # SET NX EX claims the cooldown atomically, so concurrent requests enqueue one job.
        accepted = await redis.set(key, "1", nx=True, ex=settings.manual_sync_cooldown_seconds)
        if not accepted:
            ttl = max(1, await redis.ttl(key))
            raise ProblemError(
                429,
                "sync-cooldown",
                "Sync recently requested",
                "A sync was recently requested for this repository.",
                headers={"Retry-After": str(ttl)},
            )
        job, _ = await enqueue_sync(arq, session, repo, "manual", now=now)
    except (RedisError, OSError, TimeoutError) as exc:
        raise unavailable("The sync queue is unavailable.") from exc
    response.headers["Location"] = f"/v1/sync-jobs/{job.id}"
    return job_response(job, repo)

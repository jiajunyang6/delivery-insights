from datetime import datetime, timedelta
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from insights.api.deps import get_arq, get_now, get_redis, get_session, get_settings
from insights.api.errors import ProblemError
from insights.api.params import parse_repo_path
from insights.api.schemas import RepoList, RepoStatus, SyncJobResponse
from insights.config import Settings
from insights.db.models import Repository, SyncJob
from insights.redis import sync_cooldown_key
from insights.sync.queue import enqueue_sync

router = APIRouter(prefix="/v1/repos", tags=["Repositories"])


def job_response(job: SyncJob, repo: str) -> SyncJobResponse:
    return SyncJobResponse(
        id=str(job.id),
        repo=repo,
        kind=job.kind,
        status=job.status,
        phase=job.phase,
        stats=job.stats,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        url=f"/v1/sync-jobs/{job.id}",
    )


@router.get("", response_model=RepoList)
async def repositories(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_now)],
) -> RepoList:
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
    return RepoList(items=items)


def validated_repo_path(
    owner: str, name: str, settings: Annotated[Settings, Depends(get_settings)]
) -> str:
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
    try:
        key = sync_cooldown_key(repo)
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
        raise ProblemError(
            503,
            "dependency-unavailable",
            "Dependency unavailable",
            "The sync queue is unavailable.",
        ) from exc
    response.headers["Location"] = f"/v1/sync-jobs/{job.id}"
    response.headers["Cache-Control"] = "no-store"
    return job_response(job, repo)

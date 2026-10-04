"""Sync job status lookup, the target of Location headers from sync requests."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from insights.api.deps import get_session
from insights.api.params import validate_job_id
from insights.api.responses import job_response
from insights.api.schemas import SyncJobResponse
from insights.db.models import Repository, SyncJob
from insights.snapshots.service import not_found

router = APIRouter(prefix="/v1/sync-jobs", tags=["Sync jobs"])


@router.get("/{job_id}", response_model=SyncJobResponse)
async def sync_job(
    job_id: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> SyncJobResponse:
    """Load a job by canonical UUID and return its repository/status, or raise 404."""
    identifier = validate_job_id(job_id)
    row = (
        await session.execute(
            select(SyncJob, Repository.full_name)
            .join(Repository, Repository.id == SyncJob.repo_id)
            .where(SyncJob.id == identifier)
        )
    ).first()
    if row is None:
        raise not_found()
    return job_response(row[0], row[1])

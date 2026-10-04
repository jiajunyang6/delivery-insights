"""Helpers that turn service results and ORM rows into HTTP responses."""

from starlette.responses import Response

from insights.api.schemas import SyncJobResponse
from insights.db.models import SyncJob
from insights.snapshots.caching import Reply


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


def response(reply: Reply) -> Response:
    return Response(
        reply.body,
        status_code=reply.status,
        headers=reply.headers,
        media_type=None if reply.status == 304 else "application/json",
    )

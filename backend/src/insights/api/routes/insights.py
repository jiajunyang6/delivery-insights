"""Delivery snapshots and their cited narratives; adapt service replies to HTTP."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import Response

from insights.analytics.dataset import SnapshotParams
from insights.api import deps
from insights.api.params import parse_params, validate_snapshot_id
from insights.api.schemas import Narrative, Pending, Snapshot
from insights.narrative.llm import LLMClient
from insights.narrative.service import NarrativeService
from insights.snapshots.caching import Reply
from insights.snapshots.service import SnapshotService

router = APIRouter(prefix="/v1")


def parameters(
    request: Request,
    service: Annotated[SnapshotService, Depends(deps.get_snapshot_service)],
    repo: str | None = None,
    from_date: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
) -> SnapshotParams:
    # The query arguments above only document the API in OpenAPI; parse_params reads the raw
    # query so every invalid field is reported together in one 422 problem.
    """Parse the raw snapshot query and collect validation errors into one 422 response."""
    return parse_params(request.query_params, service.settings, service.now)


@router.get(
    "/insights/delivery",
    response_model=Snapshot,
    responses={202: {"model": Pending}, 304: {}},
    tags=["Insights"],
)
async def delivery(
    request: Request,
    params: Annotated[SnapshotParams, Depends(parameters)],
    service: Annotated[SnapshotService, Depends(deps.get_snapshot_service)],
) -> Response:
    """Serve current local analytics, honoring the request's conditional ETag."""
    return response(await service.delivery(params, request.headers.get("if-none-match")))


@router.get(
    "/snapshots/{snapshot_id}/narrative",
    response_model=Narrative,
    responses={304: {}},
    tags=["Snapshots"],
)
async def narrative(
    request: Request,
    snapshot_id: str,
    service: Annotated[SnapshotService, Depends(deps.get_snapshot_service)],
) -> Response:
    """Serve a retained snapshot's English narrative, honoring conditional ETags."""
    sid = validate_snapshot_id(snapshot_id)
    llm = cast(LLMClient | None, getattr(request.app.state, "llm", None))
    return response(
        await NarrativeService(service, llm).get(sid, request.headers.get("if-none-match"))
    )


def response(reply: Reply) -> Response:
    """Convert domain reply bytes/status/headers into JSON, omitting media type for 304."""
    return Response(
        reply.body,
        status_code=reply.status,
        headers=reply.headers,
        media_type=None if reply.status == 304 else "application/json",
    )

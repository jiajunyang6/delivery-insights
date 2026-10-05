"""Delivery snapshot endpoint; answers 202 Pending while data syncs."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import Response

from insights.analytics.dataset import SnapshotParams
from insights.api import deps
from insights.api.params import parse_params
from insights.api.responses import response
from insights.api.schemas import Pending, Snapshot
from insights.snapshots.service import SnapshotService

router = APIRouter(prefix="/v1/insights", tags=["Insights"])


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


@router.get("/delivery", response_model=Snapshot, responses={202: {"model": Pending}, 304: {}})
async def delivery(
    request: Request,
    params: Annotated[SnapshotParams, Depends(parameters)],
    service: Annotated[SnapshotService, Depends(deps.get_snapshot_service)],
) -> Response:
    """Serve current local analytics, honoring the request's conditional ETag."""
    return response(await service.delivery(params, request.headers.get("if-none-match")))

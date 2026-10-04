"""Delivery snapshot and PR list endpoints; both answer 202 Pending while data syncs."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import Response

from insights.analytics.dataset import SnapshotParams
from insights.api import deps
from insights.api.params import parse_filters, parse_params
from insights.api.responses import response
from insights.api.schemas import Pending, PrPage, Snapshot
from insights.snapshots.service import SnapshotService

router = APIRouter(prefix="/v1/insights", tags=["Insights"])


def parameters(
    request: Request,
    service: Annotated[SnapshotService, Depends(deps.get_snapshot_service)],
    repo: Annotated[list[str] | None, Query()] = None,
    org: str | None = None,
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


@router.get("/delivery/prs", response_model=PrPage, responses={202: {"model": Pending}})
async def prs(
    request: Request,
    params: Annotated[SnapshotParams, Depends(parameters)],
    service: Annotated[SnapshotService, Depends(deps.get_snapshot_service)],
    status: str | None = None,
    at_risk: str = "false",
    state: str | None = None,
    location: str | None = None,
    limit: str = "50",
    cursor: str | None = None,
) -> Response:
    # As in parameters(): filter arguments are declared for OpenAPI, parsed by parse_filters.
    """Parse drilldown filters and serve a cursor page from the corresponding snapshot."""
    filters, parsed_limit, parsed_cursor = parse_filters(request.query_params)
    return response(await service.rows(params, filters, parsed_limit, parsed_cursor))

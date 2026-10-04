from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import Response

from insights.analytics.dataset import SnapshotParams
from insights.api import deps
from insights.api.caching import Reply
from insights.api.params import parse_filters, parse_params
from insights.api.schemas import Pending, PrPage, Snapshot
from insights.snapshots.service import SnapshotService

router = APIRouter(prefix="/v1/insights", tags=["Insights"])


def response(reply: Reply) -> Response:
    return Response(
        reply.body,
        status_code=reply.status,
        headers=reply.headers,
        media_type=None if reply.status == 304 else "application/json",
    )


def parameters(
    request: Request,
    service: Annotated[SnapshotService, Depends(deps.get_snapshot_service)],
    repo: Annotated[list[str] | None, Query()] = None,
    org: str | None = None,
    from_date: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
) -> SnapshotParams:
    return parse_params(request.query_params, service.settings, service.now)


@router.get("/delivery", response_model=Snapshot, responses={202: {"model": Pending}, 304: {}})
async def delivery(
    request: Request,
    params: Annotated[SnapshotParams, Depends(parameters)],
    service: Annotated[SnapshotService, Depends(deps.get_snapshot_service)],
) -> Response:
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
    filters, parsed_limit, parsed_cursor = parse_filters(request.query_params)
    return response(await service.rows(params, filters, parsed_limit, parsed_cursor))

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from insights.api.deps import get_snapshot_service
from insights.api.params import validate_snapshot_id
from insights.api.routes.insights import response
from insights.api.schemas import Snapshot
from insights.snapshot_service import SnapshotService

router = APIRouter(prefix="/v1/snapshots", tags=["Snapshots"])


@router.get("/{snapshot_id}", response_model=Snapshot, responses={304: {}})
async def snapshot(
    request: Request,
    snapshot_id: str,
    service: Annotated[SnapshotService, Depends(get_snapshot_service)],
) -> Response:
    return response(
        await service.by_id(validate_snapshot_id(snapshot_id), request.headers.get("if-none-match"))
    )

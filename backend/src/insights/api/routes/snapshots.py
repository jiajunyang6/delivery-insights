from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from insights.api.deps import get_snapshot_service
from insights.api.params import invalid, validate_snapshot_id
from insights.api.responses import response
from insights.api.schemas import Narrative, Snapshot
from insights.narrative.llm import LLMClient
from insights.narrative.service import NarrativeService
from insights.snapshots.service import SnapshotService

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


@router.get("/{snapshot_id}/narrative", response_model=Narrative, responses={304: {}})
async def narrative(
    request: Request,
    snapshot_id: str,
    service: Annotated[SnapshotService, Depends(get_snapshot_service)],
    audience: str = "manager",
    lang: Literal["en"] = "en",
) -> Response:
    sid = validate_snapshot_id(snapshot_id)
    if audience not in {"director", "manager"}:
        raise invalid("audience")
    llm = cast(LLMClient | None, getattr(request.app.state, "llm", None))
    return response(
        await NarrativeService(service, llm).get(
            sid, audience, request.headers.get("if-none-match")
        )
    )

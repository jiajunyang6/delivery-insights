"""The narrative generated for a snapshot."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from insights.api.deps import get_snapshot_service
from insights.api.params import validate_snapshot_id
from insights.api.responses import response
from insights.api.schemas import Narrative
from insights.narrative.llm import LLMClient
from insights.narrative.service import NarrativeService
from insights.snapshots.service import SnapshotService

router = APIRouter(prefix="/v1/snapshots", tags=["Snapshots"])


@router.get("/{snapshot_id}/narrative", response_model=Narrative, responses={304: {}})
async def narrative(
    request: Request,
    snapshot_id: str,
    service: Annotated[SnapshotService, Depends(get_snapshot_service)],
) -> Response:
    """Serve a retained snapshot's English narrative, honoring conditional ETags."""
    sid = validate_snapshot_id(snapshot_id)
    llm = cast(LLMClient | None, getattr(request.app.state, "llm", None))
    return response(
        await NarrativeService(service, llm).get(sid, request.headers.get("if-none-match"))
    )

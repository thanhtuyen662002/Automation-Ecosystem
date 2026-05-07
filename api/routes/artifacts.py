from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from api.dependencies import DatabaseDependency
from api.schemas import ArtifactResponse, ArtifactStatusUpdateRequest


LOGGER = logging.getLogger("api.artifacts")
router = APIRouter(prefix="/artifacts", tags=["artifacts"])

_VALID_ARTIFACT_STATUSES = {"approved", "rejected"}


class ArtifactListResponse(BaseModel):
    items: list[ArtifactResponse]


@router.get("", response_model=ArtifactListResponse)
async def list_artifacts(
    database: DatabaseDependency,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ArtifactListResponse:
    rows = await database.list_artifacts(limit=limit, offset=offset)
    LOGGER.info("artifacts_listed", extra={"event": "artifacts_listed", "count": len(rows)})
    return ArtifactListResponse(items=[ArtifactResponse.from_row(row) for row in rows])


@router.put("/{artifact_id}/status", response_model=ArtifactResponse)
async def update_artifact_status(
    artifact_id: str,
    request: ArtifactStatusUpdateRequest,
    database: DatabaseDependency,
) -> ArtifactResponse:
    if request.status not in _VALID_ARTIFACT_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{request.status}'. Must be one of: {sorted(_VALID_ARTIFACT_STATUSES)}",
        )
    existing = await database.get_artifact(artifact_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    updated = await database.update_artifact_status(artifact_id, request.status)
    if updated is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    LOGGER.info(
        "artifact_status_updated",
        extra={"event": "artifact_status_updated", "artifact_id": artifact_id, "status": request.status},
    )
    return ArtifactResponse.from_row(updated)

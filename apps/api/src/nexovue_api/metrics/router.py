"""HTTP for the metrics catalogue. No SQL and no arithmetic."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.deps import DbSession
from nexovue_api.metrics import service
from nexovue_api.metrics.schemas import MetricCatalogueResponse
from nexovue_api.projects.service import require_project
from nexovue_api.window import resolve_window

router = APIRouter(prefix="/metrics", tags=["metrics"])

ProjectId = Annotated[uuid.UUID, Query(description="Project whose metrics to list")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]


@router.get("", summary="Every metric this project reported, busiest first")
async def list_metrics(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> MetricCatalogueResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.list_metrics(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
    )

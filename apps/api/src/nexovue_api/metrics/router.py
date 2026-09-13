"""HTTP for the metrics catalogue. No SQL and no arithmetic."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query

from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.deps import DbSession
from nexovue_api.metrics import service
from nexovue_api.metrics.schemas import HistogramResponse, MetricCatalogueResponse
from nexovue_api.projects.service import require_project
from nexovue_api.window import resolve_window

router = APIRouter(prefix="/metrics", tags=["metrics"])

ProjectId = Annotated[uuid.UUID, Query(description="Project whose metrics to list")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]
# Metric names carry dots — `http.server.duration` — which a path segment
# takes unchanged. Bounded because it reaches a bound parameter, not to
# validate a name: a name we have never seen is an empty distribution, not a
# bad request.
MetricName = Annotated[str, Path(min_length=1, max_length=255)]


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


@router.get("/{metric_name}/histogram", summary="One histogram metric's distribution")
async def read_histogram(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    metric_name: MetricName,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> HistogramResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.read_histogram(
        repository,
        project_id=str(project.id),
        metric_name=metric_name,
        from_time=window_start,
        to_time=window_end,
    )

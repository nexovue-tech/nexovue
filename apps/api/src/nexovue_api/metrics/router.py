"""HTTP for the metrics catalogue. No SQL and no arithmetic."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query

from nexovue_api.auth.dependencies import CurrentIdentity, CurrentReader, TenantRepository
from nexovue_api.dashboardpanels.router import PanelReadsDep
from nexovue_api.deps import DbSession
from nexovue_api.metrics import service
from nexovue_api.metrics.schemas import (
    DurationHeatmapResponse,
    HistogramResponse,
    MetricCatalogueResponse,
    MetricSeriesListResponse,
)
from nexovue_api.projects.service import require_project, require_readable_project
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
ServiceName = Annotated[str, Query(min_length=1, max_length=255)]

# The shapes a name can arrive in. Enumerated so a typo is refused at the edge
# rather than answering with an empty catalogue a reader would take for "this
# project reports no gauges". `test_metrics.py` holds these to `queries`.
MetricShape = Literal["gauge", "sum", "histogram", "exponential histogram", "summary"]
NameMatch = Annotated[
    str | None,
    Query(max_length=255, description="Only names containing this text, case-insensitively"),
]
Shape = Annotated[MetricShape | None, Query(description="Only names that arrived in this shape")]
ReadableOnly = Annotated[bool, Query(description="Only names this console can draw")]
# The two shapes a dashboard draws as a line (F20, FR6).
SeriesShape = Annotated[Literal["gauge", "sum"], Query(description="The table the metric is in")]


@router.get("", summary="Every metric this project reported, busiest first")
async def list_metrics(
    reader: CurrentReader,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    name: NameMatch = None,
    shape: Shape = None,
    readable_only: ReadableOnly = False,
) -> MetricCatalogueResponse:
    project = await require_readable_project(db, reader=reader, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.list_metrics(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
        name_match=name,
        shape=shape,
        readable_only=readable_only,
    )


@router.get("/{metric_name}/histogram", summary="One histogram metric's distribution")
async def read_histogram(
    reader: CurrentReader,
    db: DbSession,
    repository: TenantRepository,
    metric_name: MetricName,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> HistogramResponse:
    project = await require_readable_project(db, reader=reader, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.read_histogram(
        repository,
        project_id=str(project.id),
        metric_name=metric_name,
        from_time=window_start,
        to_time=window_end,
    )


@router.get("/duration-heatmap", summary="One service's latency distribution over time")
async def read_duration_heatmap(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    service_name: ServiceName,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> DurationHeatmapResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.read_duration_heatmap(
        repository,
        project_id=str(project.id),
        service_name=service_name,
        from_time=window_start,
        to_time=window_end,
    )


@router.get("/{metric_name}/series", summary="One service's attribute combinations of a metric")
async def list_series(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    reads: PanelReadsDep,
    metric_name: MetricName,
    project_id: ProjectId,
    service_name: ServiceName,
    shape: SeriesShape,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> MetricSeriesListResponse:
    """For a dashboard's builder (F20, AC83, AC121). The first read to return attribute
    values, which may be personal data or attacker text, so `CurrentIdentity`: no API token
    reaches it, and it is off the read surface (design section 8)."""
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.list_series_within_raw_limits(
        repository,
        reads.caps,
        org_id=str(identity.org_id),
        project_id=str(project.id),
        metric_name=metric_name,
        shape=shape,
        service_name=service_name,
        from_time=window_start,
        to_time=window_end,
    )

"""Reading what real browsers measured.

`project_id` arrives as a query parameter, which makes it a value the caller
chose. It is proved to belong to the caller's organization before it reaches a
telemetry query — `require_project` is that proof, and the repository the query
runs through is bound to the session's tenant, never to anything in the URL.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.deps import DbSession
from nexovue_api.projects.service import require_project
from nexovue_api.rum import service
from nexovue_api.rum.schemas import (
    BreakdownDimension,
    WebVitalBreakdownResponse,
    WebVitalsResponse,
)
from nexovue_api.window import resolve_window

router = APIRouter(prefix="/rum", tags=["rum"])

ProjectId = Annotated[uuid.UUID, Query(description="Project to read browser data from")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]
# An enum, so a dimension the store cannot group by is refused as a 422 here
# rather than reaching a query.
Dimension = Annotated[BreakdownDimension, Query(description="Split the readings by page or device")]

# The two filters both reads accept. There is no rating filter: the rating a
# reader sees is computed from the merged p75 on the screen, where the Core Web
# Vitals thresholds live, and a second copy of them here could exclude a page
# this screen still labels poor.
DeviceClass = Annotated[str | None, Query(description="Only readings from this class of device")]
Route = Annotated[str | None, Query(description="Only readings from this page")]


@router.get("/web-vitals", summary="Each Core Web Vital's p75 over a time window")
async def get_web_vitals(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    device_class: DeviceClass = None,
    route: Route = None,
) -> WebVitalsResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.summarize_web_vitals(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
        device_class=device_class,
        route=route,
    )


@router.get(
    "/web-vitals/breakdown",
    summary="Each Core Web Vital's p75 split by page or by device class",
)
async def get_web_vitals_breakdown(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    dimension: Dimension,
    from_time: FromTime = None,
    to_time: ToTime = None,
    device_class: DeviceClass = None,
    route: Route = None,
) -> WebVitalBreakdownResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.break_down_web_vitals(
        repository,
        dimension=dimension,
        device_class=device_class,
        route=route,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
    )

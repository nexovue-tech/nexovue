"""Reading the service map.

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
from nexovue_api.servicemap import service
from nexovue_api.servicemap.schemas import ServiceMapResponse
from nexovue_api.window import clamp_limit, resolve_window

router = APIRouter(prefix="/service-map", tags=["service-map"])


@router.get("", summary="Service dependencies in a time window, busiest first")
async def get_service_map(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: Annotated[uuid.UUID, Query(description="Project to read dependencies from")],
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int | None, Query(ge=1, le=service.MAX_LIMIT)] = None,
    min_calls_per_minute: Annotated[
        float | None,
        Query(ge=0, description="Hide edges quieter than this, in calls per minute"),
    ] = None,
    failing_only: Annotated[
        bool, Query(description="Only edges that failed at least once")
    ] = False,
) -> ServiceMapResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.get_service_map(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
        limit=clamp_limit(limit, default=service.DEFAULT_LIMIT, maximum=service.MAX_LIMIT),
        min_calls_per_minute=min_calls_per_minute,
        failing_only=failing_only,
    )

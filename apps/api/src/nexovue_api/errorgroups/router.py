"""Reading error groups.

`project_id` arrives as a query parameter, which makes it a value the caller
chose. It is proved to belong to the caller's organization before it reaches a
telemetry query — `require_project` is that proof, and the repository the query
runs through is bound to the session's tenant, never to anything in the URL.

`service_name` is a caller value too, but it only narrows a read the tenant is
already entitled to, so it is bound as a parameter rather than checked.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.deps import DbSession
from nexovue_api.errorgroups import service
from nexovue_api.errorgroups.schemas import (
    ErrorGroupDetailResponse,
    ErrorGroupListResponse,
    GroupTrendResponse,
)
from nexovue_api.projects.service import require_project
from nexovue_api.window import clamp_limit, resolve_window

router = APIRouter(prefix="/error-groups", tags=["error-groups"])

ProjectId = Annotated[uuid.UUID, Query(description="Project to read errors from")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]


@router.get("", summary="Exceptions in a time window, grouped, most frequent first")
async def list_error_groups(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    service_name: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=service.MAX_LIMIT)] = None,
    new_only: Annotated[
        bool, Query(description="Only groups that began inside the window")
    ] = False,
) -> ErrorGroupListResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.list_error_groups(
        repository,
        project_id=str(project.id),
        service_name=service_name,
        from_time=window_start,
        to_time=window_end,
        limit=clamp_limit(limit, default=service.DEFAULT_LIMIT, maximum=service.MAX_LIMIT),
        new_only=new_only,
    )


@router.get("/trend", summary="How often each group happened, bucket by bucket")
async def group_trends(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    service_name: Annotated[str | None, Query(alias="service")] = None,
) -> GroupTrendResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.group_trends(
        repository,
        project_id=str(project.id),
        service_name=service_name,
        from_time=window_start,
        to_time=window_end,
    )


@router.get("/{group_id}", summary="One error group and its most recent instances")
async def get_error_group(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    group_id: str,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> ErrorGroupDetailResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.get_error_group(
        repository,
        project_id=str(project.id),
        group_id=group_id,
        from_time=window_start,
        to_time=window_end,
    )

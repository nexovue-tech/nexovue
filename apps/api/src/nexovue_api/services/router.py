"""Reading the service overview.

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
from nexovue_api.projects.service import require_project
from nexovue_api.services import service
from nexovue_api.services.schemas import EndpointListResponse, ServiceListResponse
from nexovue_api.window import clamp_limit, resolve_window

router = APIRouter(prefix="/services", tags=["services"])

ProjectId = Annotated[uuid.UUID, Query(description="Project to read service health from")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]


@router.get("", summary="Every service in a time window, worst first")
async def list_services(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    limit: Annotated[int | None, Query(ge=1, le=service.MAX_SERVICE_LIMIT)] = None,
) -> ServiceListResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.list_services(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
        limit=clamp_limit(
            limit, default=service.DEFAULT_SERVICE_LIMIT, maximum=service.MAX_SERVICE_LIMIT
        ),
    )


@router.get("/endpoints", summary="Every endpoint in the project, worst first")
async def list_project_endpoints(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    limit: Annotated[int | None, Query(ge=1, le=service.MAX_ENDPOINT_LIMIT)] = None,
) -> EndpointListResponse:
    return await _endpoints(
        identity,
        db,
        repository,
        project_id=project_id,
        service_name=None,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
    )


@router.get("/{service_name}/endpoints", summary="One service's endpoints, worst first")
async def list_service_endpoints(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    service_name: str,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    limit: Annotated[int | None, Query(ge=1, le=service.MAX_ENDPOINT_LIMIT)] = None,
) -> EndpointListResponse:
    return await _endpoints(
        identity,
        db,
        repository,
        project_id=project_id,
        service_name=service_name,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
    )


async def _endpoints(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    *,
    project_id: uuid.UUID,
    service_name: str | None,
    from_time: datetime | None,
    to_time: datetime | None,
    limit: int | None,
) -> EndpointListResponse:
    """The two endpoint routes differ only in whether a service was named."""
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.list_endpoints(
        repository,
        project_id=str(project.id),
        service_name=service_name,
        from_time=window_start,
        to_time=window_end,
        limit=clamp_limit(
            limit, default=service.DEFAULT_ENDPOINT_LIMIT, maximum=service.MAX_ENDPOINT_LIMIT
        ),
    )

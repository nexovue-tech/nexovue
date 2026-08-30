"""Reading traces.

`project_id` arrives as a query parameter, which makes it a value the caller
chose. It is proved to belong to the caller's organization before it reaches a
telemetry query — `require_project` is that proof, and the repository the query
runs through is bound to the session's tenant, never to anything in the URL.

Every filter below is likewise a caller-chosen value, and every one of them is
bound as a query parameter. Nothing a caller sends is ever spliced into SQL:
`service.build_filters` decides *which* predicate a request carries, and
`queries` holds the text of each.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.deps import DbSession
from nexovue_api.projects.service import require_project
from nexovue_api.traces import service
from nexovue_api.traces.schemas import TraceDetailResponse, TraceFacetsResponse, TraceListResponse
from nexovue_api.window import clamp_limit, resolve_window

router = APIRouter(prefix="/traces", tags=["traces"])

ProjectId = Annotated[uuid.UUID, Query(description="Project to read traces from")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]


@router.get("", summary="Traces in a time window, newest first")
async def list_traces(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    span_service: Annotated[
        str | None,
        Query(alias="service", description="Traces containing a span from this service"),
    ] = None,
    operation: Annotated[
        str | None, Query(description="Traces containing a span with this name")
    ] = None,
    status: Annotated[
        str | None, Query(description="`ERROR` for traces containing a failure, `OK` for the rest")
    ] = None,
    min_duration_ms: Annotated[float | None, Query(ge=0)] = None,
    max_duration_ms: Annotated[float | None, Query(ge=0)] = None,
    attr: Annotated[
        str | None,
        Query(description="Span attribute name, such as `db.system` or `http.request.method`"),
    ] = None,
    attr_value: Annotated[str | None, Query(description="The value `attr` must equal")] = None,
    limit: Annotated[int | None, Query(ge=1, le=service.MAX_LIMIT)] = None,
    cursor: Annotated[str | None, Query(description="Opaque marker from a previous page")] = None,
) -> TraceListResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.list_traces(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
        filters=service.build_filters(
            service_name=span_service,
            operation=operation,
            status=status,
            min_duration_ms=min_duration_ms,
            max_duration_ms=max_duration_ms,
            attribute_path=attr,
            attribute_value=attr_value,
        ),
        limit=clamp_limit(limit, default=service.DEFAULT_LIMIT, maximum=service.MAX_LIMIT),
        cursor=cursor,
    )


@router.get("/facets", summary="The services and operations this window holds")
async def facets(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> TraceFacetsResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.trace_facets(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
    )


# Declared after `/facets`: a literal path must be matched before `{trace_id}`,
# or the facets request is answered as a trace whose id is "facets".
@router.get("/{trace_id}", summary="One trace and its spans, flat")
async def get_trace(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    trace_id: str,
    project_id: ProjectId,
) -> TraceDetailResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    return await service.get_trace(repository, project_id=str(project.id), trace_id=trace_id)

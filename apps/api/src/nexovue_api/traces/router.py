"""Reading traces.

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
from nexovue_api.traces import service
from nexovue_api.traces.schemas import TraceDetailResponse, TraceListResponse
from nexovue_api.window import clamp_limit, resolve_window

router = APIRouter(prefix="/traces", tags=["traces"])

ProjectId = Annotated[uuid.UUID, Query(description="Project to read traces from")]


@router.get("", summary="Traces in a time window, newest first")
async def list_traces(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int | None, Query(ge=1, le=service.MAX_LIMIT)] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> TraceListResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.list_traces(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
        limit=clamp_limit(limit, default=service.DEFAULT_LIMIT, maximum=service.MAX_LIMIT),
        cursor=cursor,
    )


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

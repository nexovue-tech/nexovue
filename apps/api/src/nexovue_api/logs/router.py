"""Reading logs by the trace they belong to.

`trace_id` is required. This endpoint is the correlation path, not the general
log search — that arrives with the log pipeline and adds its own filters here.

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
from nexovue_api.logs import service
from nexovue_api.logs.schemas import LogsForTraceResponse
from nexovue_api.projects.service import require_project
from nexovue_api.window import clamp_limit, resolve_window

router = APIRouter(prefix="/logs", tags=["logs"])

ProjectId = Annotated[uuid.UUID, Query(description="Project to read logs from")]


@router.get("", summary="Logs emitted under one trace, and the trace they point back at")
async def logs_for_trace(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    trace_id: Annotated[str, Query(description="Trace the logs were emitted under")],
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int | None, Query(ge=1, le=service.MAX_LIMIT)] = None,
) -> LogsForTraceResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.logs_for_trace(
        repository,
        project_id=str(project.id),
        trace_id=trace_id,
        from_time=window_start,
        to_time=window_end,
        limit=clamp_limit(limit, default=service.DEFAULT_LIMIT, maximum=service.MAX_LIMIT),
    )

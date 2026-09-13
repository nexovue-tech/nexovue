"""Reading what door 4b's poller measured on a customer's database.

`project_id` arrives as a query parameter, which makes it a value the caller
chose. It is proved to belong to the caller's organization before it reaches a
telemetry query — `require_project` is that proof, and the repository the query
runs through is bound to the session's tenant, never to anything in the URL.

Both endpoints answer with an empty list while no database is connected. That is
the correct answer, not an error: "nothing polled yet" is the normal state
before a customer grants the read-only role the poller needs.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.dbstats import service
from nexovue_api.dbstats.schemas import (
    ConnectionHealthResponse,
    SlowStatementsResponse,
    StatementTrendResponse,
)
from nexovue_api.deps import DbSession
from nexovue_api.projects.service import require_project
from nexovue_api.window import clamp_limit, resolve_window

router = APIRouter(prefix="/dbstats", tags=["dbstats"])

ProjectId = Annotated[uuid.UUID, Query(description="Project the polled database belongs to")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]


@router.get("/statements", summary="Slowest normalized statements, by mean execution time")
async def slow_statements(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    limit: Annotated[int | None, Query(ge=1, le=service.MAX_STATEMENT_LIMIT)] = None,
    server: Annotated[str | None, Query(description="Only statements on this server")] = None,
    min_mean_ms: Annotated[
        float | None, Query(ge=0, description="Only statements whose worst mean reached this")
    ] = None,
) -> SlowStatementsResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.slow_statements(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
        limit=clamp_limit(
            limit, default=service.DEFAULT_STATEMENT_LIMIT, maximum=service.MAX_STATEMENT_LIMIT
        ),
        server=server,
        min_mean_ms=min_mean_ms,
    )


@router.get("/connections", summary="Backend connections by state")
async def connection_health(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> ConnectionHealthResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.connection_health(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
    )


@router.get("/statements/trend", summary="Each statement's cost, bucket by bucket")
async def statement_trends(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> StatementTrendResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.statement_trends(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
    )

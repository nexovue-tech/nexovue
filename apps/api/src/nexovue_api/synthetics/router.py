"""Reading what the door 2 checks measured.

`checks/` serves the definitions a customer edits; this serves the readings the
prober published against them. They are split because they are different stores
with different lifetimes — a definition is a Postgres row a person owns, and a
reading is a ClickHouse point under a retention TTL.

`project_id` arrives as a query parameter, which makes it a value the caller
chose. It is proved to belong to the caller's organization before it reaches a
telemetry query — `require_project` is that proof, and the repository the query
runs through is bound to the session's tenant, never to anything in the URL.

`check_id` needs no such proof. It filters inside a read that is already scoped
to the tenant, so another organization's check id selects nothing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.deps import DbSession
from nexovue_api.projects.service import require_project
from nexovue_api.synthetics import service
from nexovue_api.synthetics.schemas import (
    CheckResultListResponse,
    CheckRunListResponse,
    CheckTrendResponse,
    PhaseBreakdownResponse,
)
from nexovue_api.window import clamp_limit, resolve_window

router = APIRouter(prefix="/synthetics", tags=["synthetics"])

ProjectId = Annotated[uuid.UUID, Query(description="Project the checks belong to")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]


@router.get("/checks", summary="Every check's readings over a window")
async def list_check_results(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> CheckResultListResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.list_check_results(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
    )


@router.get(
    "/checks/{check_id}/phases",
    summary="Where one check's time went: dns, tcp, tls, ttfb, transfer",
)
async def phase_breakdown(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    check_id: uuid.UUID,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    bucket_seconds: Annotated[
        int | None,
        Query(
            ge=service.MIN_BUCKET_SECONDS,
            le=service.MAX_BUCKET_SECONDS,
            description="Bucket width; defaults to one that fits the window",
        ),
    ] = None,
) -> PhaseBreakdownResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.phase_breakdown(
        repository,
        project_id=str(project.id),
        check_id=str(check_id),
        from_time=window_start,
        to_time=window_end,
        bucket_seconds=service.resolve_bucket_seconds(window_start, window_end, bucket_seconds),
    )


@router.get("/checks/{check_id}/runs", summary="Individual probes, newest first")
async def list_runs(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    check_id: uuid.UUID,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    limit: Annotated[int | None, Query(ge=1, le=service.MAX_RUN_LIMIT)] = None,
) -> CheckRunListResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.list_runs(
        repository,
        project_id=str(project.id),
        check_id=str(check_id),
        from_time=window_start,
        to_time=window_end,
        limit=clamp_limit(limit, default=service.DEFAULT_RUN_LIMIT, maximum=service.MAX_RUN_LIMIT),
    )


@router.get("/checks/trend", summary="Each check's uptime and duration, bucket by bucket")
async def check_trends(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> CheckTrendResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.check_trends(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
    )

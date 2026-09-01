"""Hosts and containers, read from the metrics a customer's collector sends.

Kubernetes pod and node views are Phase 2 and deliberately absent.

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
from nexovue_api.infrastructure import service
from nexovue_api.infrastructure.schemas import NodeListResponse, SignalListResponse
from nexovue_api.projects.service import require_project
from nexovue_api.window import resolve_window

router = APIRouter(prefix="/infrastructure", tags=["infrastructure"])

ProjectId = Annotated[uuid.UUID, Query(description="Project whose hosts to read")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]


@router.get("/hosts", summary="Every host, container and reporter in the window")
async def list_nodes(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> NodeListResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.list_nodes(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
    )


@router.get("/signals", summary="Which infrastructure metrics this project has sent")
async def list_signals(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> SignalListResponse:
    """The empty state, answered.

    No signals means no collector is scraping the host; signals with an old
    `last_seen` means one was and has stopped. The hosts endpoint cannot tell
    those apart, and they need different advice.
    """
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.list_signals(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
    )

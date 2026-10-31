"""Findings (F6, ADR-022). The reads are on the read surface (ADR-017); the three
actions are a person's (FR19 to FR21, FR23).

`project_id` is a value the caller chose, so it is proved to belong to the
caller's organization, and to a token's scope, before anything is read.
`/findings/{finding_id}` takes no project, so the scope is compared against the
row it loads, as `get_incident` does. Acknowledge and false alarm are every
working role's, as operating alerts is; a new normal moves what detection judges
by, and is an admin's (D11).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Query

from nexovue_api.auth.dependencies import CurrentReader, TenantRepository
from nexovue_api.auth.policy import MayConfigureDetection, MayOperateAlerts
from nexovue_api.deps import DbSession
from nexovue_api.findings import service
from nexovue_api.findings.schemas import (
    FalseAlarmRequest,
    FeedState,
    FindingChanged,
    FindingDetail,
    FindingForMember,
    FindingListResponse,
    FindingStats,
)
from nexovue_api.projects.service import require_readable_project

router = APIRouter(prefix="/findings", tags=["findings"])

ProjectId = Annotated[uuid.UUID, Query(description="Project to read findings from")]


@router.get("", summary="Open, then acknowledged, then closed findings; at most 200")
async def list_findings(
    reader: CurrentReader,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    state: Annotated[FeedState, Query(description="`open` includes acknowledged")] = "all",
    muted: Annotated[bool, Query(description="Read the muted findings instead")] = False,
) -> FindingListResponse:
    project = await require_readable_project(db, reader=reader, project_id=project_id)
    return await service.list_findings(
        db,
        repository,
        org_id=reader.org_id,
        project_id=project.id,
        state=state,
        muted=muted,
        now=datetime.now(UTC),
    )


# Before `/{finding_id}`, which would otherwise claim the path and refuse it as a
# malformed id.
@router.get("/stats", summary="Findings opened and false alarms over the last days")
async def finding_stats(
    reader: CurrentReader,
    db: DbSession,
    project_id: ProjectId,
    days: Annotated[
        int, Query(ge=1, le=service.MAX_STATS_DAYS, description="The window, ending now")
    ] = service.DEFAULT_STATS_DAYS,
) -> FindingStats:
    project = await require_readable_project(db, reader=reader, project_id=project_id)
    return await service.finding_stats(
        db, org_id=reader.org_id, project_id=project.id, days=days, now=datetime.now(UTC)
    )


@router.get("/{finding_id}", summary="One finding, with its readings while they last")
async def get_finding(
    reader: CurrentReader, db: DbSession, repository: TenantRepository, finding_id: uuid.UUID
) -> FindingForMember | FindingDetail:
    """A signed-in person also sees who acknowledged it, the false alarm's reason, and
    what a new normal would be."""
    return await service.get_finding(
        db,
        repository,
        org_id=reader.org_id,
        finding_id=finding_id,
        project_scope=reader.project_scope,
        for_member=not reader.is_token,
        now=datetime.now(UTC),
    )


@router.post("/{finding_id}/acknowledge", summary="Say you are handling an open finding")
async def acknowledge_finding(
    identity: MayOperateAlerts, db: DbSession, repository: TenantRepository, finding_id: uuid.UUID
) -> FindingChanged:
    return await service.acknowledge(
        db, repository, actor=identity.user, finding_id=finding_id, now=datetime.now(UTC)
    )


@router.post(
    "/{finding_id}/false-alarm",
    summary="Close a finding as a false alarm, or mark one that recovered",
)
async def mark_false_alarm(
    identity: MayOperateAlerts,
    db: DbSession,
    repository: TenantRepository,
    finding_id: uuid.UUID,
    # Optional, as its one field is: the reason is the person's to give or not.
    request: Annotated[FalseAlarmRequest | None, Body()] = None,
) -> FindingChanged:
    return await service.mark_false_alarm(
        db,
        repository,
        actor=identity.user,
        finding_id=finding_id,
        reason=None if request is None else request.reason,
        now=datetime.now(UTC),
    )


@router.post("/{finding_id}/new-normal", summary="Make an open finding's level the new normal")
async def accept_new_normal(
    identity: MayConfigureDetection,
    db: DbSession,
    repository: TenantRepository,
    finding_id: uuid.UUID,
) -> FindingChanged:
    return await service.accept_new_normal(
        db, repository, actor=identity.user, finding_id=finding_id, now=datetime.now(UTC)
    )

"""Check definitions, which the console edits and the scheduler reads.

The scheduler re-reads `checks` on a timer, so a check saved here starts running
within one refresh interval and nothing is deployed to make that happen.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayAdminister
from nexovue_api.checks import service
from nexovue_api.checks.schemas import (
    CheckListResponse,
    CheckStateChangeListResponse,
    CheckSummary,
    CreateCheckRequest,
    CreatedCheck,
    DeletionImpact,
    HeartbeatToken,
    UpdateCheckRequest,
)
from nexovue_api.deps import AppSettings, DbSession

router = APIRouter(prefix="/checks", tags=["checks"])


@router.get("", summary="Every check on one project")
async def list_checks(
    identity: CurrentIdentity,
    db: DbSession,
    settings: AppSettings,
    project_id: uuid.UUID,
) -> CheckListResponse:
    return await service.list_for_project(
        db,
        org_id=identity.org_id,
        project_id=project_id,
        available_locations=settings.probe_locations,
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="Define a check")
async def create_check(
    identity: MayAdminister,
    db: DbSession,
    settings: AppSettings,
    request: CreateCheckRequest,
) -> CreatedCheck:
    return await service.create(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        request=request,
        available_locations=settings.probe_locations,
    )


@router.get("/{check_id}", summary="One check, with its confirmed state")
async def get_check(identity: CurrentIdentity, db: DbSession, check_id: uuid.UUID) -> CheckSummary:
    return await service.get(db, org_id=identity.org_id, check_id=check_id)


@router.patch("/{check_id}", summary="Edit a check, or pause it")
async def update_check(
    identity: MayAdminister,
    db: DbSession,
    settings: AppSettings,
    check_id: uuid.UUID,
    request: UpdateCheckRequest,
) -> CheckSummary:
    return await service.update(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        check_id=check_id,
        request=request,
        available_locations=settings.probe_locations,
    )


@router.delete("/{check_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a check")
async def delete_check(identity: MayAdminister, db: DbSession, check_id: uuid.UUID) -> None:
    await service.remove(
        db, org_id=identity.org_id, actor_user_id=identity.user.id, check_id=check_id
    )


@router.get("/{check_id}/deletion-impact", summary="What deleting this check loses")
async def deletion_impact(
    identity: CurrentIdentity, db: DbSession, check_id: uuid.UUID
) -> DeletionImpact:
    return await service.deletion_impact(db, org_id=identity.org_id, check_id=check_id)


@router.get("/{check_id}/state-changes", summary="Confirmed state changes, newest first")
async def state_changes(
    identity: CurrentIdentity, db: DbSession, check_id: uuid.UUID
) -> CheckStateChangeListResponse:
    return await service.state_changes(db, org_id=identity.org_id, check_id=check_id)


@router.post(
    "/{check_id}/heartbeat-token",
    status_code=status.HTTP_201_CREATED,
    summary="Issue a new heartbeat URL; the previous one stops working",
)
async def issue_heartbeat_token(
    identity: MayAdminister, db: DbSession, check_id: uuid.UUID
) -> HeartbeatToken:
    return await service.issue_heartbeat_token(
        db, org_id=identity.org_id, actor_user_id=identity.user.id, check_id=check_id
    )

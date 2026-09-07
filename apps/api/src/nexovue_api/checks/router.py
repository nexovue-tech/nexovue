"""Door 2 check definitions, which the UI edits and the scheduler reads.

The scheduler re-reads this table on a timer, so a check saved here starts
running within one refresh interval and nothing is deployed to make that happen.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayAdminister
from nexovue_api.checks import service
from nexovue_api.checks.schemas import (
    CheckListResponse,
    CheckSummary,
    CreateCheckRequest,
    UpdateCheckRequest,
)
from nexovue_api.deps import DbSession

router = APIRouter(prefix="/checks", tags=["checks"])


@router.get("", summary="Every check on one project")
async def list_checks(
    identity: CurrentIdentity,
    db: DbSession,
    project_id: uuid.UUID,
) -> CheckListResponse:
    return await service.list_for_project(db, org_id=identity.org_id, project_id=project_id)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Define a check")
async def create_check(
    identity: MayAdminister,
    db: DbSession,
    request: CreateCheckRequest,
) -> CheckSummary:
    return await service.create(
        db, org_id=identity.org_id, actor_user_id=identity.user.id, request=request
    )


@router.patch("/{check_id}", summary="Edit a check, or pause it")
async def update_check(
    identity: MayAdminister,
    db: DbSession,
    check_id: uuid.UUID,
    request: UpdateCheckRequest,
) -> CheckSummary:
    return await service.update(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        check_id=check_id,
        request=request,
    )


@router.delete("/{check_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a check")
async def delete_check(
    identity: MayAdminister,
    db: DbSession,
    check_id: uuid.UUID,
) -> None:
    await service.remove(
        db, org_id=identity.org_id, actor_user_id=identity.user.id, check_id=check_id
    )

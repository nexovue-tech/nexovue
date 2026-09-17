"""Maintenance windows for a project's checks (FR36)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayAdminister
from nexovue_api.deps import DbSession
from nexovue_api.maintenance import service
from nexovue_api.maintenance.schemas import (
    MaintenanceWindowListResponse,
    MaintenanceWindowSummary,
    WriteMaintenanceWindowRequest,
)

router = APIRouter(prefix="/maintenance-windows", tags=["maintenance windows"])


@router.get("", summary="Every maintenance window on one project")
async def list_windows(
    identity: CurrentIdentity, db: DbSession, project_id: uuid.UUID
) -> MaintenanceWindowListResponse:
    return await service.list_windows(db, org_id=identity.org_id, project_id=project_id)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Schedule a maintenance window")
async def create_window(
    identity: MayAdminister, db: DbSession, request: WriteMaintenanceWindowRequest
) -> MaintenanceWindowSummary:
    return await service.create(
        db, org_id=identity.org_id, actor_user_id=identity.user.id, request=request
    )


@router.put("/{window_id}", summary="Replace a maintenance window")
async def update_window(
    identity: MayAdminister,
    db: DbSession,
    window_id: uuid.UUID,
    request: WriteMaintenanceWindowRequest,
) -> MaintenanceWindowSummary:
    return await service.update(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        window_id=window_id,
        request=request,
    )


@router.delete(
    "/{window_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a maintenance window"
)
async def delete_window(identity: MayAdminister, db: DbSession, window_id: uuid.UUID) -> None:
    await service.remove(
        db, org_id=identity.org_id, actor_user_id=identity.user.id, window_id=window_id
    )

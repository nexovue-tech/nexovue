"""Import checks: preview what a cURL command, OpenAPI file or Postman collection holds,
then confirm the reviewed plan (FR11)."""

from __future__ import annotations

from fastapi import APIRouter, status

from nexovue_api.auth.policy import MayAdminister
from nexovue_api.checkimports import service
from nexovue_api.checkimports.schemas import (
    ImportConfirmed,
    ImportConfirmRequest,
    ImportPlan,
    ImportPreviewRequest,
)
from nexovue_api.deps import AppSettings, DbSession

router = APIRouter(prefix="/check-imports", tags=["check imports"])


@router.post("/preview", summary="What an import would create; nothing is saved")
async def preview_import(
    identity: MayAdminister, db: DbSession, request: ImportPreviewRequest
) -> ImportPlan:
    return await service.preview(db, org_id=identity.org_id, request=request)


@router.post(
    "/confirm",
    status_code=status.HTTP_201_CREATED,
    summary="Create the reviewed checks, with their secrets and variables",
)
async def confirm_import(
    identity: MayAdminister,
    db: DbSession,
    settings: AppSettings,
    request: ImportConfirmRequest,
) -> ImportConfirmed:
    return await service.confirm(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        request=request,
        check_secret_key=settings.check_secret_key,
        available_locations=settings.probe_locations,
    )

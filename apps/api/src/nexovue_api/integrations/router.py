"""Door 4b connectors, which the UI creates and the scheduler polls."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.deps import DbSession
from nexovue_api.integrations import service
from nexovue_api.integrations.schemas import (
    CreatedIntegrationResponse,
    CreateIntegrationRequest,
    IntegrationListResponse,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("", summary="Every connector on one project")
async def list_integrations(
    identity: CurrentIdentity,
    db: DbSession,
    project_id: uuid.UUID,
) -> IntegrationListResponse:
    return await service.list_for_project(db, org_id=identity.org_id, project_id=project_id)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Connect a cloud account")
async def create_integration(
    identity: CurrentIdentity,
    db: DbSession,
    request: CreateIntegrationRequest,
) -> CreatedIntegrationResponse:
    return await service.create(
        db, org_id=identity.org_id, actor_user_id=identity.user.id, request=request
    )


@router.delete(
    "/{integration_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Disconnect an account"
)
async def delete_integration(
    identity: CurrentIdentity,
    db: DbSession,
    integration_id: uuid.UUID,
) -> None:
    await service.remove(
        db, org_id=identity.org_id, actor_user_id=identity.user.id, integration_id=integration_id
    )

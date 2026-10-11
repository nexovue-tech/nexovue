"""Secrets and variables for a project's checks.

A secret's value goes in and never comes out: there is no route that returns
one, and the list names secrets without their values (FR20, EC12).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayManageCheckSecrets
from nexovue_api.checksecrets import service
from nexovue_api.checksecrets.schemas import (
    CheckSecretListResponse,
    CheckSecretSummary,
    CheckVariableListResponse,
    CheckVariableSummary,
    SecretName,
    VariableName,
    WriteSecretRequest,
    WriteVariableRequest,
)
from nexovue_api.deps import AppSettings, DbSession

router = APIRouter(prefix="/projects/{project_id}", tags=["check secrets"])


@router.get("/check-secrets", summary="Every secret's name, never its value")
async def list_secrets(
    identity: CurrentIdentity, db: DbSession, project_id: uuid.UUID
) -> CheckSecretListResponse:
    return await service.list_secrets(db, org_id=identity.org_id, project_id=project_id)


@router.put("/check-secrets/{name}", summary="Store or replace a secret")
async def write_secret(
    identity: MayManageCheckSecrets,
    db: DbSession,
    settings: AppSettings,
    project_id: uuid.UUID,
    name: SecretName,
    request: WriteSecretRequest,
) -> CheckSecretSummary:
    return await service.write_secret(
        db,
        org_id=identity.org_id,
        project_id=project_id,
        name=name,
        value=request.value,
        box=service.secret_box(settings.check_secret_keys),
        actor_user_id=identity.user.id,
    )


@router.delete(
    "/check-secrets/{name}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a secret"
)
async def delete_secret(
    identity: MayManageCheckSecrets, db: DbSession, project_id: uuid.UUID, name: SecretName
) -> None:
    await service.delete_secret(
        db,
        org_id=identity.org_id,
        project_id=project_id,
        name=name,
        actor_user_id=identity.user.id,
    )


@router.get("/check-variables", summary="Every variable, with its value")
async def list_variables(
    identity: CurrentIdentity, db: DbSession, project_id: uuid.UUID
) -> CheckVariableListResponse:
    return await service.list_variables(db, org_id=identity.org_id, project_id=project_id)


@router.put("/check-variables/{name}", summary="Store or replace a variable")
async def write_variable(
    identity: MayManageCheckSecrets,
    db: DbSession,
    project_id: uuid.UUID,
    name: VariableName,
    request: WriteVariableRequest,
) -> CheckVariableSummary:
    return await service.write_variable(
        db, org_id=identity.org_id, project_id=project_id, name=name, value=request.value
    )


@router.delete(
    "/check-variables/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a variable",
)
async def delete_variable(
    identity: MayManageCheckSecrets, db: DbSession, project_id: uuid.UUID, name: VariableName
) -> None:
    await service.delete_variable(db, org_id=identity.org_id, project_id=project_id, name=name)

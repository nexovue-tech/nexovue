"""The ingest keys of the signed-in organization.

`project_id` and `key_id` are values the caller chose, so neither is trusted:
the project is proved to belong to the caller's organization before it is used
as a filter, and the key is looked up by id *and* org_id together, so another
tenant's key is a 404 rather than a row.

Revocation is a POST to a named action rather than a DELETE. The row is not
deleted — it is marked withdrawn, and an audit needs to see what was withdrawn.
Creating and revoking both ask what the caller's role is: one grants ingest for
the whole organization, the other stops it irreversibly.

Creating answers 201 with the plaintext key in the body. That body is the only
place the secret ever exists outside the customer's own configuration, and there
is no second route that could read it back.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from nexovue_api.apikeys import service
from nexovue_api.apikeys.schemas import (
    ApiKeyCreated,
    ApiKeyCreateRequest,
    ApiKeyListResponse,
    ApiKeySummary,
)
from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.deps import DbSession
from nexovue_api.projects.service import require_project

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

ProjectId = Annotated[uuid.UUID, Query(description="Project whose keys to list")]


@router.get("", summary="Every ingest key in a project")
async def list_api_keys(
    identity: CurrentIdentity, db: DbSession, project_id: ProjectId
) -> ApiKeyListResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    return await service.list_for_project(db, org_id=identity.org_id, project_id=project.id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Mint an ingest key, leaving existing ones live",
)
async def create_api_key(
    identity: CurrentIdentity, db: DbSession, request: ApiKeyCreateRequest
) -> ApiKeyCreated:
    project = await require_project(db, org_id=identity.org_id, project_id=request.project_id)
    return await service.create(
        db,
        org_id=identity.org_id,
        actor=identity.user,
        project_id=project.id,
        name=request.name,
    )


@router.post("/{key_id}/revoke", summary="Withdraw an ingest key")
async def revoke_api_key(
    identity: CurrentIdentity, db: DbSession, key_id: uuid.UUID
) -> ApiKeySummary:
    return await service.revoke(db, org_id=identity.org_id, actor=identity.user, key_id=key_id)

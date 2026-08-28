"""The ingest keys of the signed-in organization.

`project_id` and `key_id` are values the caller chose, so neither is trusted:
the project is proved to belong to the caller's organization before it is used
as a filter, and the key is looked up by id *and* org_id together, so another
tenant's key is a 404 rather than a row.

Revocation is a POST to a named action rather than a DELETE. The row is not
deleted — it is marked withdrawn, and an audit needs to see what was withdrawn.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from nexovue_api.apikeys import service
from nexovue_api.apikeys.schemas import ApiKeyListResponse, ApiKeySummary
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


@router.post("/{key_id}/revoke", summary="Withdraw an ingest key")
async def revoke_api_key(
    identity: CurrentIdentity, db: DbSession, key_id: uuid.UUID
) -> ApiKeySummary:
    return await service.revoke(
        db, org_id=identity.org_id, actor_user_id=identity.user.id, key_id=key_id
    )

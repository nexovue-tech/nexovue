"""The API tokens of the signed-in organization (F36). HTTP only.

Creating answers 201 with the plaintext in the body. That body is the only place
the secret ever exists outside the customer's own configuration, and there is no
second route that could read it back.

Revoking is a POST to a named action rather than a DELETE, as the ingest keys
are: the row is not deleted — it is marked withdrawn, and an audit needs to see
what was withdrawn.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status

from nexovue_api.apitokens import service
from nexovue_api.apitokens.schemas import (
    ApiTokenCreated,
    ApiTokenCreateRequest,
    ApiTokenListResponse,
    ApiTokenSummary,
)
from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayManageApiTokens
from nexovue_api.deps import DbSession
from nexovue_api.projects.service import require_project

router = APIRouter(prefix="/api-tokens", tags=["api-tokens"])


@router.get("", summary="Every API token this organization has")
async def list_api_tokens(identity: CurrentIdentity, db: DbSession) -> ApiTokenListResponse:
    """Readable by everyone signed in: a token is the organization's, and no
    secret is in the answer. Only creating and revoking ask for a role."""
    return await service.list_for_organization(db, org_id=identity.org_id, now=datetime.now(UTC))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Mint a read token, shown once",
)
async def create_api_token(
    identity: MayManageApiTokens, db: DbSession, request: ApiTokenCreateRequest
) -> ApiTokenCreated:
    project_id = None
    if request.project_id is not None:
        # Proved to be the caller's before it is stored as a scope; another
        # tenant's project is a 404 here, as it is everywhere else.
        project = await require_project(db, org_id=identity.org_id, project_id=request.project_id)
        project_id = project.id
    return await service.create(
        db,
        org_id=identity.org_id,
        actor=identity.user,
        name=request.name,
        project_id=project_id,
        expires_in_days=request.expires_in_days,
        now=datetime.now(UTC),
    )


@router.post("/{token_id}/revoke", summary="Withdraw an API token")
async def revoke_api_token(
    identity: MayManageApiTokens, db: DbSession, token_id: uuid.UUID
) -> ApiTokenSummary:
    return await service.revoke(
        db,
        org_id=identity.org_id,
        actor=identity.user,
        token_id=token_id,
        now=datetime.now(UTC),
    )

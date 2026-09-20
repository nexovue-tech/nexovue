"""Deploy notifications: one public door, two signed-in reads.

``POST /deploys/webhook/{project_id}`` is the only unauthenticated write in the
control plane. Two consequences shape this module:

* the body is read as **raw bytes**, not as a parsed model — a signature is over
  the bytes that arrived, and a pydantic body parameter would have FastAPI
  answer 422 on a forged request before the signature was ever looked at;
* the read is bounded before it is buffered, because an unauthenticated caller
  chooses how much it sends.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query, Request, status

from nexovue_api.auth.dependencies import CurrentReader
from nexovue_api.auth.policy import MayManageCredentials
from nexovue_api.deploys import service, signatures
from nexovue_api.deploys.schemas import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    DeployEventListResponse,
    DeployEventSummary,
    WebhookSecretsResponse,
)
from nexovue_api.deps import DbSession
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.projects.service import refuse_out_of_scope

router = APIRouter(prefix="/deploys", tags=["deploys"])

# A deploy notification is a handful of fields. Anything larger is not one, and
# buffering it to find that out would be the attack rather than the defence.
MAX_BODY_BYTES: Final = 64 * 1024

BODY_TOO_LARGE_DETAIL: Final = f"A deploy notification must be at most {MAX_BODY_BYTES} bytes."


@router.post(
    "/webhook/{project_id}",
    status_code=status.HTTP_201_CREATED,
    summary="Record a deploy, signed with the project's deploy secret",
)
async def receive_deploy(
    db: DbSession,
    request: Request,
    project_id: uuid.UUID,
) -> DeployEventSummary:
    return await service.record_from_webhook(
        db,
        project_id=project_id,
        body=await _bounded_body(request),
        signature=request.headers.get(signatures.SIGNATURE_HEADER),
        # The sender's id for this delivery. GitHub's signature carries no
        # timestamp, so this is the only thing that makes a captured request
        # distinguishable from a genuine retry.
        delivery_id=request.headers.get(signatures.DELIVERY_HEADER),
    )


@router.get("", summary="Recent deploys on one project")
async def list_deploys(
    reader: CurrentReader,
    db: DbSession,
    project_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> DeployEventListResponse:
    """On the read surface (FR17): a person, or one of the organization's tokens."""
    refuse_out_of_scope(reader, project_id)
    return await service.list_for_project(
        db, org_id=reader.org_id, project_id=project_id, limit=limit
    )


@router.get(
    "/webhook-secrets",
    summary="The deploy secrets this project's webhook will accept",
)
async def read_webhook_secrets(
    # The secrets themselves, so a credential action even though it only reads.
    identity: MayManageCredentials,
    db: DbSession,
    project_id: uuid.UUID,
) -> WebhookSecretsResponse:
    return await service.webhook_secrets(
        db,
        org_id=identity.org_id,
        project_id=project_id,
        actor_user_id=identity.user.id,
    )


@router.post(
    "/webhook-secrets",
    status_code=status.HTTP_201_CREATED,
    summary="Mint a deploy secret, leaving existing ones live",
)
async def create_webhook_secret(
    identity: MayManageCredentials,
    db: DbSession,
    project_id: uuid.UUID,
) -> WebhookSecretsResponse:
    return await service.create_webhook_secret(
        db, org_id=identity.org_id, project_id=project_id, actor_user_id=identity.user.id
    )


@router.delete(
    "/webhook-secrets/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw a deploy secret",
)
async def revoke_webhook_secret(
    identity: MayManageCredentials,
    db: DbSession,
    secret_id: uuid.UUID,
) -> None:
    await service.revoke_webhook_secret(
        db, org_id=identity.org_id, secret_id=secret_id, actor_user_id=identity.user.id
    )


async def _bounded_body(request: Request) -> bytes:
    """The raw bytes, refusing an oversized body without holding it in memory.

    Streamed rather than `await request.body()`: Content-Length is a value the
    client chose, so the cap has to be enforced on what actually arrives.
    """
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_BODY_BYTES:
            raise InvalidRequestError(BODY_TOO_LARGE_DETAIL)
        chunks.append(chunk)
    return b"".join(chunks)

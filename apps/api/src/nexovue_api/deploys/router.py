"""Deploy notifications: one public door, two signed-in reads.

``POST /deploys/webhook/{project_id}`` is the only unauthenticated write in the
control plane (F28's batch envelope, design *Routes*). Two consequences shape this module:

* the body is read as **raw bytes**, not as a parsed model — a signature is over
  the bytes that arrived, and a pydantic body parameter would have FastAPI
  answer 422 on a forged request before the signature was ever looked at;
* the read is bounded before it is buffered, because an unauthenticated caller
  chooses how much it sends.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query, Request, status
from nexovue_platform.checks.sealing import SealedBox
from pydantic import AwareDatetime

from nexovue_api.auth.dependencies import CurrentReader
from nexovue_api.auth.policy import MayManageCredentials
from nexovue_api.deploys import service, signatures
from nexovue_api.deploys.schemas import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    DeployBatchAnswer,
    DeployEventListResponse,
    MintedWebhookSecretResponse,
    WebhookSecretsResponse,
)
from nexovue_api.deps import AppSettings, DbSession
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.projects.service import refuse_out_of_scope
from nexovue_api.ratelimit.limiter import Decision, RateLimiter
from nexovue_api.ratelimit.policy import DEPLOYS_ACCEPTED

router = APIRouter(prefix="/deploys", tags=["deploys"])

# F28 FR6 (a starting guess): room for 100 items with every field at its limit in plain
# ASCII. An unauthenticated caller chooses how much it sends, so the bound comes first.
MAX_BODY_BYTES: Final = 512 * 1024

BODY_TOO_LARGE_DETAIL: Final = "A deploy request must be at most 512 KiB; send fewer items."

# Whose hourly allowance a batch spends (FR13): the project's, once signed.
_LIMIT_SCOPE: Final = "project"


def _secret_box(settings: AppSettings) -> SealedBox:
    return service.secret_box(settings.credential_keys)


DeploySecretBox = Annotated[SealedBox, Depends(_secret_box)]


def _rate_limiter(request: Request) -> RateLimiter:
    """The middleware's limiter, which the lifespan puts on the app: the hourly count is
    the route's, once the signature has held, so it cannot be the middleware's (FR13)."""
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


DeployLimiter = Annotated[RateLimiter, Depends(_rate_limiter)]


@router.post(
    "/webhook/{project_id}",
    summary="Record a batch of deploys, signed with one of the project's deploy secrets",
)
async def receive_deploys(
    db: DbSession,
    request: Request,
    project_id: uuid.UUID,
    box: DeploySecretBox,
    limiter: DeployLimiter,
) -> DeployBatchAnswer:
    async def charge(cost: int) -> Decision:
        return await limiter.consume(
            scope=_LIMIT_SCOPE, identity=str(project_id), quota=DEPLOYS_ACCEPTED, cost=cost
        )

    return await service.record_from_webhook(
        db,
        project_id=project_id,
        body=await _bounded_body(request),
        signature=request.headers.get(signatures.SIGNATURE_HEADER),
        box=box,
        charge=charge,
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
    summary="The deploy secrets this project accepts, and what its intake last saw",
)
async def read_webhook_secrets(
    # A credential's settings, though no value is ever read back (F28 D7).
    identity: MayManageCredentials,
    db: DbSession,
    project_id: uuid.UUID,
) -> WebhookSecretsResponse:
    return await service.webhook_secrets(db, org_id=identity.org_id, project_id=project_id)


@router.post(
    "/webhook-secrets",
    status_code=status.HTTP_201_CREATED,
    summary="Mint a deploy secret, shown this once, leaving existing ones live",
)
async def create_webhook_secret(
    identity: MayManageCredentials,
    db: DbSession,
    project_id: uuid.UUID,
    box: DeploySecretBox,
) -> MintedWebhookSecretResponse:
    return await service.create_webhook_secret(
        db,
        org_id=identity.org_id,
        project_id=project_id,
        actor_user_id=identity.user.id,
        box=box,
    )


@router.delete(
    "/webhook-secrets/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw a deploy secret, and optionally remove what it sent since a time",
)
async def revoke_webhook_secret(
    identity: MayManageCredentials,
    db: DbSession,
    secret_id: uuid.UUID,
    remove_since: Annotated[AwareDatetime | None, Query()] = None,
) -> None:
    await service.revoke_webhook_secret(
        db,
        org_id=identity.org_id,
        secret_id=secret_id,
        actor_user_id=identity.user.id,
        remove_since=remove_since,
    )


@router.delete(
    "/{deploy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove one deploy, and work out its release again",
)
async def delete_deploy(
    identity: MayManageCredentials,
    db: DbSession,
    deploy_id: uuid.UUID,
) -> None:
    await service.remove_deploy(
        db, org_id=identity.org_id, deploy_id=deploy_id, actor_user_id=identity.user.id
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

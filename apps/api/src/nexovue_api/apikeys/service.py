"""Listing keys, and withdrawing one.

Revocation is one-way. A key is revoked because its secret has stopped being a
secret, and un-revoking would put a credential somebody else holds back into
service; the replacement for a revoked key is a new key. Nothing here can clear
`revoked_at`, and there is no route that could.

Revoking a project's last live key stops its ingest, and that is allowed: a
customer who has leaked their only key must be able to stop the leak. The
console sees it in the list it refreshes afterwards.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.apikeys import queries
from nexovue_api.apikeys.schemas import ApiKeyListResponse, ApiKeySummary
from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import ApiKey
from nexovue_api.errors.exceptions import NotFoundError

# Says nothing about whether the key exists in some other organization.
NO_SUCH_KEY_DETAIL: Final = "No such API key."


async def list_for_project(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> ApiKeyListResponse:
    keys = await queries.list_keys(db, org_id=org_id, project_id=project_id)
    return ApiKeyListResponse(api_keys=[_summarize(key) for key in keys])


async def revoke(
    db: AsyncSession, *, org_id: uuid.UUID, actor_user_id: uuid.UUID, key_id: uuid.UUID
) -> ApiKeySummary:
    """Withdraw a key. Idempotent: an already revoked key is returned unchanged.

    The audit record is added to this transaction, so the revocation and the
    record of it commit together — a withdrawn key with no trail of who
    withdrew it is not a state this can reach.
    """
    key = await queries.lock_key(db, org_id=org_id, key_id=key_id)
    if key is None:
        raise NotFoundError(NO_SUCH_KEY_DETAIL)

    if key.revoked_at is None:
        # The application clock, so the response carries the value without a
        # second round trip to read back a server-side `now()`.
        key.revoked_at = datetime.now(UTC)
        audit.record_api_key_revoked(db, api_key=key, actor_user_id=actor_user_id)

    return _summarize(key)


def _summarize(key: ApiKey) -> ApiKeySummary:
    return ApiKeySummary(
        id=key.id,
        project_id=key.project_id,
        name=key.name,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
    )

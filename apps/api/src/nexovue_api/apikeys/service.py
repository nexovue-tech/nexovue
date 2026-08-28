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
from nexovue_api.data.postgres.models import ApiKey, User
from nexovue_api.errors.exceptions import NotFoundError, PermissionDeniedError

# Says nothing about whether the key exists in some other organization.
NO_SUCH_KEY_DETAIL: Final = "No such API key."

# Of the seven secret characters the stored prefix carries.
HINT_CHARACTERS: Final = 4

MAY_NOT_REVOKE_DETAIL: Final = "Only an owner or admin can revoke an API key."

# Revocation is irreversible and stops a project's ingest, so it is the first
# action in this codebase to ask what a member's role is. Broader role
# enforcement arrives with F26; this is not that, it is one destructive action
# that should never have been available to everyone.
ROLES_THAT_MAY_REVOKE: Final = frozenset({"owner", "admin"})


async def list_for_project(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> ApiKeyListResponse:
    keys = await queries.list_keys(db, org_id=org_id, project_id=project_id)
    return ApiKeyListResponse(api_keys=[_summarize(key) for key in keys])


async def revoke(
    db: AsyncSession, *, org_id: uuid.UUID, actor: User, key_id: uuid.UUID
) -> ApiKeySummary:
    """Withdraw a key. Idempotent: an already revoked key is returned unchanged.

    The audit record is added to this transaction, so the revocation and the
    record of it commit together — a withdrawn key with no trail of who
    withdrew it is not a state this can reach.
    """
    if actor.role not in ROLES_THAT_MAY_REVOKE:
        raise PermissionDeniedError(MAY_NOT_REVOKE_DETAIL)

    key = await queries.lock_key(db, org_id=org_id, key_id=key_id)
    if key is None:
        raise NotFoundError(NO_SUCH_KEY_DETAIL)

    if key.revoked_at is None:
        # The application clock, so the response carries the value without a
        # second round trip to read back a server-side `now()`.
        key.revoked_at = datetime.now(UTC)
        audit.record_api_key_revoked(db, api_key=key, actor_user_id=actor.id)

    return _summarize(key)


def _summarize(key: ApiKey) -> ApiKeySummary:
    return ApiKeySummary(
        id=key.id,
        project_id=key.project_id,
        name=key.name,
        key_hint=_hint(key.key_prefix),
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
    )


def _hint(key_prefix: str) -> str:
    """Enough of a key to recognise it, not enough to be worth stealing.

    A customer who finds a leaked key needs to know which row to revoke. The
    stored prefix is the scheme plus seven characters of the secret, so the
    scheme is shown whole and only the last four of those seven — leaving
    eighteen of the twenty-two random characters undisclosed, while still
    telling one of a handful of keys from another.
    """
    scheme, _, secret = key_prefix.rpartition("_")
    return f"{scheme}_…{secret[-HINT_CHARACTERS:]}"

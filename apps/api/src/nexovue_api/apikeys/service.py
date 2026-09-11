"""Minting keys, listing them, and withdrawing one — for both kinds.

**A key has a kind, and the kind is which table it lives in**. The
secret, server-side kind is an `ApiKey`: hashed, verified in constant time,
disclosed once. The public, browser-side kind is a `SiteKey`: stored in the
clear, restricted by origin, readable back as often as the customer asks.

The two sets of routines below are deliberately not one parameterised set. They
share nothing but a role check, and a shared routine taking a `kind` argument is
the shape in which a public key gets minted, listed or summarized where a secret
one was meant — the exact failure the two tables exist to make unreachable.

Creation and revocation are the two halves of rotation, and they exist together
so a leak has a way out. Without a create, revoking is a choice between leaving a
leaked key live and stopping your own ingest for good.

Revocation is one-way. A key is revoked because its secret has stopped being a
secret, and un-revoking would put a credential somebody else holds back into
service; the replacement for a revoked key is a new key. Nothing here can clear
`revoked_at`, and there is no route that could.

Revoking a project's last live key stops its ingest, and that is allowed: a
customer who has leaked their only key must be able to stop the leak. The
console sees it in the list it refreshes afterwards.

The plaintext of a minted key exists in `create`'s return value and nowhere else
— not in a log line, not in the audit entry, not in the row. There is no code
path here that can produce it a second time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.apikeys import queries
from nexovue_api.apikeys.schemas import (
    ApiKeyCreated,
    ApiKeyListResponse,
    ApiKeySummary,
    SiteKeyListResponse,
    SiteKeySummary,
)
from nexovue_api.apikeys.sitekeys import generate_site_key
from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import API_KEY_PREFIX_LENGTH, ApiKey, SiteKey, User
from nexovue_api.errors.exceptions import (
    InternalError,
    NotFoundError,
    PermissionDeniedError,
)
from nexovue_api.signup.apikeys import generate_api_key, hash_api_key

# Says nothing about whether the key exists in some other organization.
NO_SUCH_KEY_DETAIL: Final = "No such API key."

# Of the seven secret characters the stored prefix carries.
HINT_CHARACTERS: Final = 4

MAY_NOT_REVOKE_DETAIL: Final = "Only an owner or admin can revoke an API key."
MAY_NOT_CREATE_DETAIL: Final = "Only an owner or admin can create an API key."

NO_SUCH_SITE_KEY_DETAIL: Final = "No such site key."
MAY_NOT_REVOKE_SITE_KEY_DETAIL: Final = "Only an owner or admin can revoke a site key."
MAY_NOT_CREATE_SITE_KEY_DETAIL: Final = "Only an owner or admin can create a site key."

# Both actions grant or withdraw ingest for the whole organization, so both ask
# what a member's role is. Broader role enforcement arrives with F26; this is not
# that, it is the two credential actions that should never have been everyone's.
ROLES_THAT_MAY_MANAGE_KEYS: Final = frozenset({"owner", "admin"})

# `key_prefix` is unique over sixteen characters, so a fresh key can genuinely
# collide. Bounded so an exhausted keyspace fails loudly instead of spinning.
MAX_KEY_ATTEMPTS: Final = 10

NO_KEY_AVAILABLE_DETAIL: Final = (
    "Could not allocate an API key. Report this with the correlation id."
)


async def allocate_unused_key(db: AsyncSession) -> str:
    """A fresh plaintext key whose lookup prefix nobody has claimed.

    Shared with signup so a tenant's first key and its fifth are minted by one
    routine — two implementations of "is this prefix free?" would be two answers.
    """
    for _ in range(MAX_KEY_ATTEMPTS):
        candidate = generate_api_key()
        if not await queries.key_prefix_is_taken(db, candidate[:API_KEY_PREFIX_LENGTH]):
            return candidate
    raise InternalError(NO_KEY_AVAILABLE_DETAIL)


async def create(
    db: AsyncSession, *, org_id: uuid.UUID, actor: User, project_id: uuid.UUID, name: str
) -> ApiKeyCreated:
    """Mint a key for one of the caller's own projects.

    Existing keys are left live, which is what makes rotation possible: deploy the
    new key, confirm the old one has gone quiet, then revoke it. The one exception
    belongs to the customer, not to this function — a key that has leaked should
    be revoked first and replaced second, and the console words the two actions so
    that choice is made deliberately.

    The caller has already proved the project is theirs. `org_id` is taken from
    the session either way, so the row cannot be written into another tenant.
    """
    if actor.role not in ROLES_THAT_MAY_MANAGE_KEYS:
        raise PermissionDeniedError(MAY_NOT_CREATE_DETAIL)

    plaintext = await allocate_unused_key(db)
    key = ApiKey(
        org_id=org_id,
        project_id=project_id,
        name=name.strip(),
        key_prefix=plaintext[:API_KEY_PREFIX_LENGTH],
        key_hash=hash_api_key(plaintext),
    )
    db.add(key)
    # Flushed so the row has its id and `created_at` before it is summarized, and
    # so the audit entry names a key that exists.
    await db.flush()
    audit.record_api_key_created(db, api_key=key, actor_user_id=actor.id)

    return ApiKeyCreated(api_key=_summarize(key), key=SecretStr(plaintext))


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
    if actor.role not in ROLES_THAT_MAY_MANAGE_KEYS:
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


async def allocate_unused_site_key(db: AsyncSession) -> str:
    """A fresh public key nobody has claimed. Bounded like the secret one."""
    for _ in range(MAX_KEY_ATTEMPTS):
        candidate = generate_site_key()
        if not await queries.site_key_is_taken(db, candidate):
            return candidate
    raise InternalError(NO_KEY_AVAILABLE_DETAIL)


async def create_site_key(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor: User,
    project_id: uuid.UUID,
    name: str,
    allowed_origins: list[str],
) -> SiteKeySummary:
    """Mint a public browser key for one of the caller's own projects.

    The return is an ordinary summary, not a `…Created` carrying a `SecretStr`:
    there is no one-time disclosure to make, because this value is readable in
    the customer's page source and this endpoint can hand it back forever.

    Gated to the same roles as the secret kind. It grants less — browser
    telemetry for one project — but it is still a door onto a customer's ingest
    and their bill, so it is not everyone's to open.
    """
    if actor.role not in ROLES_THAT_MAY_MANAGE_KEYS:
        raise PermissionDeniedError(MAY_NOT_CREATE_SITE_KEY_DETAIL)

    key = SiteKey(
        org_id=org_id,
        project_id=project_id,
        name=name.strip(),
        public_key=await allocate_unused_site_key(db),
        allowed_origins=allowed_origins,
    )
    db.add(key)
    # Flushed so the row has its id and `created_at`, and so the audit entry
    # names a key that exists.
    await db.flush()
    audit.record_site_key_created(db, site_key=key, actor_user_id=actor.id)

    return _summarize_site_key(key)


async def list_site_keys_for_project(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> SiteKeyListResponse:
    keys = await queries.list_site_keys(db, org_id=org_id, project_id=project_id)
    return SiteKeyListResponse(site_keys=[_summarize_site_key(key) for key in keys])


async def revoke_site_key(
    db: AsyncSession, *, org_id: uuid.UUID, actor: User, key_id: uuid.UUID
) -> SiteKeySummary:
    """Withdraw a public key. Idempotent, and one-way, like the secret kind.

    Revoking is the only lever a public key has: it cannot be un-leaked, because
    it was never secret. What it can be is retired when a site stops using it.
    """
    if actor.role not in ROLES_THAT_MAY_MANAGE_KEYS:
        raise PermissionDeniedError(MAY_NOT_REVOKE_SITE_KEY_DETAIL)

    key = await queries.lock_site_key(db, org_id=org_id, key_id=key_id)
    if key is None:
        raise NotFoundError(NO_SUCH_SITE_KEY_DETAIL)

    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        audit.record_site_key_revoked(db, site_key=key, actor_user_id=actor.id)

    return _summarize_site_key(key)


def _summarize_site_key(key: SiteKey) -> SiteKeySummary:
    """The whole key, every time. There is no hint, because there is nothing to
    withhold — `public_key` is the value the customer pasted into their page."""
    return SiteKeySummary(
        id=key.id,
        project_id=key.project_id,
        name=key.name,
        public_key=key.public_key,
        allowed_origins=list(key.allowed_origins),
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

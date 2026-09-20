"""Minting API tokens, listing them, withdrawing one — and recognising one.

The plaintext of a minted token exists in `create`'s return value and nowhere
else: not in a log line, not in the audit entry, not in the row. There is no
code path here that can produce it a second time.

Revocation is one-way, as an ingest key's is. A token is revoked because its
secret has stopped being one, and un-revoking would put a credential somebody
else holds back into service.

`reader_for` is the other half: it turns a presented string into the
organization and scope it may read, or refuses it. It is the only place that
decides a token is live, so "revoked", "expired" and "its project is gone" are
answered in one sentence each rather than in whichever route asked first.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Final

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.apitokens import queries
from nexovue_api.apitokens.schemas import (
    ApiTokenCreated,
    ApiTokenListResponse,
    ApiTokenSummary,
)
from nexovue_api.apitokens.tokens import generate_api_token, hash_api_token
from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import API_TOKEN_PREFIX_LENGTH, ApiToken, User
from nexovue_api.errors.exceptions import NotFoundError, UnauthenticatedError
from nexovue_api.projects import queries as project_queries

# Says nothing about whether the token exists in some other organization.
NO_SUCH_TOKEN_DETAIL: Final = "No such API token."

# Of the characters the stored prefix carries.
HINT_CHARACTERS: Final = 4

# One sentence each, and each true: an assistant or a CI log repeats these.
TOKEN_UNKNOWN: Final = "That API token is not one of ours, or it has been revoked."
TOKEN_EXPIRED: Final = "That API token expired on {day}. Create a new one in Settings → API tokens."
TOKEN_PROJECT_GONE: Final = "That API token reads a project that has been deleted."

LIVE: Final = "live"
REVOKED: Final = "revoked"
EXPIRED: Final = "expired"


async def create(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor: User,
    name: str,
    project_id: uuid.UUID | None,
    expires_in_days: int,
    now: datetime,
) -> ApiTokenCreated:
    """Mint a token for the caller's organization.

    The caller has already proved the project is theirs when they named one;
    `org_id` comes from the session either way, so the row cannot be written
    into another tenant.
    """
    plaintext = generate_api_token()
    token = ApiToken(
        org_id=org_id,
        project_id=project_id,
        name=name.strip(),
        role="read",
        token_prefix=plaintext[:API_TOKEN_PREFIX_LENGTH],
        token_hash=hash_api_token(plaintext),
        created_by=actor.id,
        created_at=now,
        expires_at=now + timedelta(days=expires_in_days),
    )
    db.add(token)
    # Flushed so the row has its id before it is summarized, and so the audit
    # entry names a token that exists.
    await db.flush()
    audit.record_api_token_created(db, token=token, actor_user_id=actor.id)

    return ApiTokenCreated(
        api_token=_summarize(token, maker=actor.name or actor.email, requests_today=0, now=now),
        token=SecretStr(plaintext),
    )


async def list_for_organization(
    db: AsyncSession, *, org_id: uuid.UUID, now: datetime
) -> ApiTokenListResponse:
    rows = await queries.list_tokens(db, org_id=org_id)
    spent = await queries.requests_on(db, org_id=org_id, day=now.date())
    return ApiTokenListResponse(
        api_tokens=[
            _summarize(
                token,
                maker=name or email,
                requests_today=spent.get(token.id, 0),
                now=now,
            )
            for token, name, email in rows
        ]
    )


async def revoke(
    db: AsyncSession, *, org_id: uuid.UUID, actor: User, token_id: uuid.UUID, now: datetime
) -> ApiTokenSummary:
    """Withdraw a token. Idempotent: an already revoked one is returned unchanged.

    The audit record joins this transaction, so the revocation and the record of
    who made it commit together.
    """
    token = await queries.lock_token(db, org_id=org_id, token_id=token_id)
    if token is None:
        raise NotFoundError(NO_SUCH_TOKEN_DETAIL)

    if token.revoked_at is None:
        token.revoked_at = now
        audit.record_api_token_revoked(db, token=token, actor_user_id=actor.id)

    spent = await queries.requests_on(db, org_id=org_id, day=now.date())
    return _summarize(token, maker=None, requests_today=spent.get(token.id, 0), now=now)


async def token_for(db: AsyncSession, presented: str, *, now: datetime) -> ApiToken:
    """The live token a caller presented, or a 401 saying why it is not one.

    Revoked and unknown share an answer on purpose: a caller holding a withdrawn
    token learns nothing about whether it was ever real, and a caller guessing
    learns nothing at all. Expiry is told plainly — the customer owns that date,
    and "it stopped working" with no reason is the worse failure.
    """
    token = await queries.find_by_hash(db, hash_api_token(presented))
    if token is None or token.revoked_at is not None:
        raise UnauthenticatedError(TOKEN_UNKNOWN)
    if token.expires_at <= now:
        raise UnauthenticatedError(TOKEN_EXPIRED.format(day=token.expires_at.date().isoformat()))
    if token.project_id is not None:
        project = await project_queries.find_project(
            db, org_id=token.org_id, project_id=token.project_id
        )
        if project is None:
            raise UnauthenticatedError(TOKEN_PROJECT_GONE)
    return token


def status_of(token: ApiToken, *, now: datetime) -> str:
    if token.revoked_at is not None:
        return REVOKED
    return EXPIRED if token.expires_at <= now else LIVE


def _summarize(
    token: ApiToken, *, maker: str | None, requests_today: int, now: datetime
) -> ApiTokenSummary:
    return ApiTokenSummary(
        id=token.id,
        name=token.name,
        project_id=token.project_id,
        token_hint=_hint(token.token_prefix),
        created_by_name=maker,
        created_at=token.created_at,
        expires_at=token.expires_at,
        revoked_at=token.revoked_at,
        last_used_at=token.last_used_at,
        requests_today=requests_today,
        status=status_of(token, now=now),
    )


def _hint(token_prefix: str) -> str:
    """`nvt_live_…a9Qz`: enough to tell two tokens apart, far short of the secret."""
    scheme, _, secret = token_prefix.rpartition("_")
    return f"{scheme}_…{secret[-HINT_CHARACTERS:]}"


def today(now: datetime) -> date:
    """The day a request is counted against, in UTC — the day the counters use."""
    return now.astimezone(UTC).date()

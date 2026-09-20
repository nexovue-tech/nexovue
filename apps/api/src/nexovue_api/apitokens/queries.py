"""API token reads.

Every statement filters `org_id`, with one named exception — `find_by_hash`,
which asks a question about the whole system because the hash is what a caller
presents before any organization is known. `tests/test_postgres_tenancy_structure.py`
carries it in EXEMPT with that reason, as it does the ingest keys' prefix lookup.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import ApiToken, ApiTokenUsageDaily, User


async def find_by_hash(session: AsyncSession, token_hash: str) -> ApiToken | None:
    """The token a caller presented, whoever it belongs to.

    Deliberately unscoped: the hash is unique across every tenant, and it is
    what *establishes* the tenant — there is no `org_id` to filter by yet. The
    caller is refused unless the row it returns is live, which `service.py`
    decides.
    """
    statement = select(ApiToken).where(ApiToken.token_hash == token_hash).limit(1)
    token: ApiToken | None = await session.scalar(statement)
    return token


async def list_tokens(
    session: AsyncSession, *, org_id: uuid.UUID
) -> Sequence[tuple[ApiToken, str | None, str | None]]:
    """Every token of one organization, revoked and expired ones included: a
    withdrawn credential is still history, as a revoked ingest key is.

    The maker's name and address come from an outer join, so a token whose
    maker has been removed still lists — with no name rather than no row.
    """
    statement = (
        select(ApiToken, User.name, User.email)
        .join(User, User.id == ApiToken.created_by, isouter=True)
        .where(ApiToken.org_id == org_id)
        .order_by(ApiToken.created_at.desc(), ApiToken.id.desc())
    )
    return [(token, name, email) for token, name, email in await session.execute(statement)]


async def lock_token(
    session: AsyncSession, *, org_id: uuid.UUID, token_id: uuid.UUID
) -> ApiToken | None:
    """One token, held for the rest of the transaction. Another tenant's is None.

    Locked for the reason the ingest keys are: revoking reads `revoked_at` and
    then writes it, and two concurrent revocations would each record that they
    were the one that withdrew it.
    """
    statement = (
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.org_id == org_id).with_for_update()
    )
    locked: ApiToken | None = await session.scalar(statement)
    return locked


async def requests_on(
    session: AsyncSession, *, org_id: uuid.UUID, day: date
) -> dict[uuid.UUID, int]:
    """What each of this organization's tokens spent on one day.

    Joined through `api_tokens` rather than read straight from the counters:
    the counter table carries no `org_id`, and this is what proves a row
    belongs to the caller before it is shown to them.
    """
    statement = (
        select(ApiTokenUsageDaily.token_id, ApiTokenUsageDaily.requests)
        .join(ApiToken, ApiToken.id == ApiTokenUsageDaily.token_id)
        .where(ApiToken.org_id == org_id, ApiTokenUsageDaily.day == day)
    )
    return dict((await session.execute(statement)).all())  # type: ignore[arg-type]

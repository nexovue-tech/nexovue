"""API key reads.

Every statement filters `org_id`, with one named exception: `key_prefix_is_taken`
asks a question about the whole system, because the prefix is unique across every
tenant. `tests/test_postgres_tenancy_structure.py` carries it in EXEMPT with that
reason, so the exception is a recorded decision rather than an omission.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import ApiKey


async def list_keys(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[ApiKey]:
    """A project's keys, revoked ones included: a withdrawn key is still history."""
    statement = (
        select(ApiKey)
        .where(ApiKey.org_id == org_id, ApiKey.project_id == project_id)
        .order_by(ApiKey.created_at, ApiKey.id)
    )
    return (await session.scalars(statement)).all()


async def lock_key(session: AsyncSession, *, org_id: uuid.UUID, key_id: uuid.UUID) -> ApiKey | None:
    """One key, held for the rest of the transaction. Another tenant's is None.

    Locked because revoking reads `revoked_at` and then writes it: without the
    lock, two concurrent revocations would each believe they were the one that
    withdrew the key, and each would write an audit record saying so.
    """
    statement = select(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == org_id).with_for_update()
    key: ApiKey | None = await session.scalar(statement)
    return key


async def key_prefix_is_taken(session: AsyncSession, key_prefix: str) -> bool:
    """Deliberately unscoped: `uq_api_keys_key_prefix` is global, so scoping this
    would let two organizations claim one prefix and make the lookup ambiguous."""
    statement = select(ApiKey.id).where(ApiKey.key_prefix == key_prefix).limit(1)
    return await session.scalar(statement) is not None

"""Ingest key reads, for both kinds.

Secret keys are read out of `api_keys`, public site keys out of `site_keys`, and
no statement here reads both. That is the whole safety argument of ADR 0086 in
one file: a list or a lookup cannot return the other kind by accident, because
the kind is which table the `select` names.

Every statement filters `org_id`, with two named exceptions — `key_prefix_is_taken`
and `site_key_is_taken` — which ask a question about the whole system, because
both values are unique across every tenant. `tests/test_postgres_tenancy_structure.py`
carries them in EXEMPT with that reason, so each exception is a recorded decision
rather than an omission.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import ApiKey, SiteKey


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


async def list_site_keys(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[SiteKey]:
    """A project's public keys, revoked ones included, for the same reason
    `list_keys` includes them: a withdrawn key is still history."""
    statement = (
        select(SiteKey)
        .where(SiteKey.org_id == org_id, SiteKey.project_id == project_id)
        .order_by(SiteKey.created_at, SiteKey.id)
    )
    return (await session.scalars(statement)).all()


async def lock_site_key(
    session: AsyncSession, *, org_id: uuid.UUID, key_id: uuid.UUID
) -> SiteKey | None:
    """One public key, held for the rest of the transaction. Locked for the same
    reason `lock_key` is: revoking reads `revoked_at` then writes it, and two
    concurrent revocations would each write an audit record claiming the deed."""
    statement = (
        select(SiteKey).where(SiteKey.id == key_id, SiteKey.org_id == org_id).with_for_update()
    )
    key: SiteKey | None = await session.scalar(statement)
    return key


async def site_key_is_taken(session: AsyncSession, public_key: str) -> bool:
    """Deliberately unscoped, exactly as `key_prefix_is_taken` is:
    `uq_site_keys_public_key` is global, and the receiver resolves a browser
    request by this value alone, so two tenants holding one would be ambiguous."""
    statement = select(SiteKey.id).where(SiteKey.public_key == public_key).limit(1)
    return await session.scalar(statement) is not None

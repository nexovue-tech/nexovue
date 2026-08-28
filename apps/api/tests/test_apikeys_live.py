"""Auditing and revocation against real Postgres.

The offline suites prove the code cannot construct a cross-tenant write. This
one proves the database agrees: real constraints, real `uuidv7()` defaults, a
real row lock, and two tenants created through the same service a real signup
uses.

Everything runs inside one transaction that is rolled back on the way out, so
the suite leaves the development database exactly as it found it. It skips
itself when Postgres is not up, so the offline suites still run without docker.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.apikeys import service as apikeys
from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import ApiKey, AuditLogEntry
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

# The receiver's own lookup, copied from apps/receiver/internal/auth/postgres.go.
# Asserted verbatim so that "the control plane revoked it" and "the ingest path
# stops accepting it" are proven to be the same fact, not two beliefs.
RECEIVER_LOOKUP: Final = text(
    "SELECT org_id::text, project_id::text, key_hash "
    "FROM api_keys WHERE key_prefix = :key_prefix AND revoked_at IS NULL"
)


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or 5432), REACHABILITY_TIMEOUT_SECONDS
        ):
            return True
    except OSError:
        return False


if not _reachable(POSTGRES_DSN):
    pytest.skip(
        "Postgres is not running — start it with `just dev` to run the live "
        "audit and revocation suite",
        allow_module_level=True,
    )


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A session in a transaction that is always rolled back."""
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _new_tenant(db: AsyncSession) -> ProvisionedTenant:
    """A tenant, created the way signup creates one. Unique so nothing collides."""
    unique = uuid.uuid4().hex[:12]
    return await provision_tenant(
        db,
        organization_name=f"Live Test {unique}",
        email=f"owner-{unique}@live.test",
        password="correct-horse-battery",
    )


async def _entries(db: AsyncSession, org_id: uuid.UUID) -> Sequence[AuditLogEntry]:
    statement = (
        select(AuditLogEntry).where(AuditLogEntry.org_id == org_id).order_by(AuditLogEntry.id)
    )
    return (await db.scalars(statement)).all()


async def _only_key(db: AsyncSession, org_id: uuid.UUID) -> ApiKey:
    key: ApiKey | None = await db.scalar(select(ApiKey).where(ApiKey.org_id == org_id))
    assert key is not None, "signup did not create a key"
    return key


async def _receiver_sees(db: AsyncSession, key_prefix: str) -> Any:
    result = await db.execute(RECEIVER_LOOKUP, {"key_prefix": key_prefix})
    return result.first()


# ------------------------------------------------------------------- the audit


async def test_signing_up_records_the_organization_and_its_first_key(db: AsyncSession) -> None:
    """The one class of history that cannot be backfilled is written at source."""
    tenant = await _new_tenant(db)

    entries = await _entries(db, tenant.organization.id)

    assert {entry.action for entry in entries} == {
        audit.ORGANIZATION_CREATED,
        audit.API_KEY_CREATED,
    }
    assert all(entry.actor_user_id == tenant.user.id for entry in entries)


async def test_the_audit_rows_commit_with_the_tenant_that_caused_them(db: AsyncSession) -> None:
    """One transaction, so `at` is the transaction's clock for every row."""
    tenant = await _new_tenant(db)
    await db.flush()

    entries = await _entries(db, tenant.organization.id)

    assert len({entry.at for entry in entries}) == 1


async def test_no_audit_row_carries_anything_that_could_be_replayed(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    key = await _only_key(db, tenant.organization.id)

    recorded = " ".join(
        f"{entry.action} {entry.target}" for entry in await _entries(db, key.org_id)
    )

    assert tenant.api_key not in recorded
    assert key.key_prefix not in recorded
    assert key.key_hash not in recorded
    assert tenant.user.password_hash is not None
    assert tenant.user.password_hash not in recorded


# -------------------------------------------------------------- the revocation


async def test_revoking_stops_the_receiver_from_resolving_the_key(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    key = await _only_key(db, tenant.organization.id)
    assert await _receiver_sees(db, key.key_prefix) is not None, (
        "the key was not live to begin with"
    )

    await apikeys.revoke(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, key_id=key.id
    )
    await db.flush()

    assert await _receiver_sees(db, key.key_prefix) is None


async def test_revoking_records_who_did_it_in_the_same_transaction(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    key = await _only_key(db, tenant.organization.id)

    await apikeys.revoke(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, key_id=key.id
    )
    await db.flush()

    revocations = [
        entry
        for entry in await _entries(db, tenant.organization.id)
        if entry.action == audit.API_KEY_REVOKED
    ]
    assert len(revocations) == 1
    assert revocations[0].actor_user_id == tenant.user.id
    assert revocations[0].target == f"{audit.API_KEY_TARGET}:{key.id}"


async def test_an_organization_cannot_revoke_another_organizations_key(db: AsyncSession) -> None:
    """The isolation invariant, held by the database and not by a caller's care."""
    attacker = await _new_tenant(db)
    victim = await _new_tenant(db)
    victims_key = await _only_key(db, victim.organization.id)

    with pytest.raises(NotFoundError):
        await apikeys.revoke(
            db,
            org_id=attacker.organization.id,
            actor_user_id=attacker.user.id,
            key_id=victims_key.id,
        )
    await db.flush()

    await db.refresh(victims_key)
    assert victims_key.revoked_at is None
    assert await _receiver_sees(db, victims_key.key_prefix) is not None
    assert [entry.action for entry in await _entries(db, victim.organization.id)] == [
        audit.ORGANIZATION_CREATED,
        audit.API_KEY_CREATED,
    ]


async def test_revoking_twice_records_the_event_once(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    key = await _only_key(db, tenant.organization.id)

    first = await apikeys.revoke(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, key_id=key.id
    )
    second = await apikeys.revoke(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, key_id=key.id
    )
    await db.flush()

    assert first.revoked_at == second.revoked_at
    revocations = [
        entry
        for entry in await _entries(db, tenant.organization.id)
        if entry.action == audit.API_KEY_REVOKED
    ]
    assert len(revocations) == 1


async def test_a_listing_shows_the_revoked_key_rather_than_hiding_it(db: AsyncSession) -> None:
    """A withdrawn key is still history the console has to be able to show."""
    tenant = await _new_tenant(db)
    key = await _only_key(db, tenant.organization.id)

    await apikeys.revoke(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, key_id=key.id
    )
    listing = await apikeys.list_for_project(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )

    assert [summary.id for summary in listing.api_keys] == [key.id]
    assert listing.api_keys[0].revoked_at is not None

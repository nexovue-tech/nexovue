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
import re
import socket
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.apikeys import service as apikeys
from nexovue_api.apikeys.schemas import ApiKeyCreated
from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import API_KEY_PREFIX_LENGTH, ApiKey, AuditLogEntry
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.signup.apikeys import hash_api_key
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

REACHABILITY_TIMEOUT_SECONDS: Final = 1.0


def _plaintext(created: ApiKeyCreated) -> str:
    """The minted secret, asked for by name.

    `ApiKeyCreated.key` is a `SecretStr`, so nothing renders it by accident.
    A test that asserts about the plaintext has to say so, here.
    """
    return created.key.get_secret_value()


# The receiver's own lookup, copied from apps/receiver/internal/auth/postgres.go.
# Asserted verbatim so that "the control plane revoked it" and "the ingest path
# stops accepting it" are proven to be the same fact, not two beliefs.
RECEIVER_LOOKUP: Final = text(
    "SELECT org_id::text, project_id::text, key_hash "
    "FROM api_keys WHERE key_prefix = :key_prefix AND revoked_at IS NULL"
)

# The receiver's `last_used_at` write, likewise copied verbatim from
# `markUsed` in the same file. The guarantee under test is the SQL's, not a
# Python reimplementation of it: this is the statement that actually runs.
RECEIVER_MARK_USED: Final = text(
    "UPDATE api_keys SET last_used_at = :at "
    "WHERE key_prefix = :key_prefix AND revoked_at IS NULL "
    "AND (last_used_at IS NULL OR last_used_at < :at)"
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


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="the live audit and revocation suite",
    remedy="Postgres is not running — start it with `just dev`",
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


async def _receiver_marks_used(db: AsyncSession, key_prefix: str, at: datetime) -> None:
    await db.execute(RECEIVER_MARK_USED, {"key_prefix": key_prefix, "at": at})


async def _key_by_prefix(db: AsyncSession, key_prefix: str) -> ApiKey:
    key: ApiKey | None = await db.scalar(select(ApiKey).where(ApiKey.key_prefix == key_prefix))
    assert key is not None, "no key with that prefix"
    await db.refresh(key)
    return key


# ------------------------------------------- the statements this suite borrows

# Where the two constants above were copied from. Copies drift, and a drifted
# copy makes every assertion below a statement about a query nothing runs.
RECEIVER_SOURCE: Final = (
    Path(__file__).resolve().parents[3] / "apps" / "receiver" / "internal" / "auth" / "postgres.go"
)

_GO_CONSTANT: Final = re.compile(r"^const (\w+) = `([^`]*)`", re.MULTILINE)

# pgx binds by position; SQLAlchemy binds by name. Same statement, two dialects.
GO_PLACEHOLDERS: Final = {"$1": ":key_prefix", "$2": ":at"}


def _normalized(statement: str) -> str:
    """Whitespace and placeholder style removed, so only the SQL is compared."""
    for positional, named in GO_PLACEHOLDERS.items():
        statement = statement.replace(positional, named)
    return " ".join(statement.split())


def _go_constants() -> dict[str, str]:
    return {
        name: _normalized(body) for name, body in _GO_CONSTANT.findall(RECEIVER_SOURCE.read_text())
    }


@pytest.mark.parametrize(
    "go_name,borrowed",
    [("lookupByPrefix", RECEIVER_LOOKUP), ("markUsed", RECEIVER_MARK_USED)],
)
def test_the_statement_this_suite_runs_is_the_one_the_receiver_runs(
    go_name: str, borrowed: Any
) -> None:
    """The link that makes the rest of this file evidence about the ingest path.

    Without it, someone could change the receiver's SQL and every test here would
    keep passing against the old statement — proving a fact about code that no
    longer exists.
    """
    constants = _go_constants()
    assert go_name in constants, (
        f"{RECEIVER_SOURCE.name} no longer declares `{go_name}`. It found {sorted(constants)}."
    )
    assert _normalized(str(borrowed)) == constants[go_name], (
        f"the copy of `{go_name}` in this suite has drifted from the receiver's"
    )


# ------------------------------------------------------------------ the minting


async def test_a_minted_key_authenticates_on_the_ingest_path(db: AsyncSession) -> None:
    """The whole point of the endpoint: a key it returns actually works.

    Asserted through the receiver's own statement and the receiver's own hash, so
    "the console minted a key" and "the ingest path accepts it" are one fact.
    """
    tenant = await _new_tenant(db)

    created = await apikeys.create(
        db,
        org_id=tenant.organization.id,
        actor=tenant.user,
        project_id=tenant.project.id,
        name="replacement",
    )
    await db.flush()

    row = await _receiver_sees(db, _plaintext(created)[:API_KEY_PREFIX_LENGTH])
    assert row is not None, "the receiver cannot resolve the key we just minted"
    org_id, project_id, key_hash = row
    assert uuid.UUID(org_id) == tenant.organization.id
    assert uuid.UUID(project_id) == tenant.project.id
    assert key_hash == hash_api_key(_plaintext(created))


async def test_a_rotation_leaves_both_keys_live_until_the_old_one_is_revoked(
    db: AsyncSession,
) -> None:
    """Create, deploy, verify, revoke — the standard order needs an overlap.

    A create that withdrew the previous key would make every rotation an outage,
    so this asserts the overlap exists and that revoking the old one afterwards
    leaves the new one untouched.
    """
    tenant = await _new_tenant(db)
    original = await _only_key(db, tenant.organization.id)

    created = await apikeys.create(
        db,
        org_id=tenant.organization.id,
        actor=tenant.user,
        project_id=tenant.project.id,
        name="replacement",
    )
    await db.flush()
    replacement_prefix = _plaintext(created)[:API_KEY_PREFIX_LENGTH]

    assert await _receiver_sees(db, original.key_prefix) is not None
    assert await _receiver_sees(db, replacement_prefix) is not None

    await apikeys.revoke(db, org_id=tenant.organization.id, actor=tenant.user, key_id=original.id)
    await db.flush()

    assert await _receiver_sees(db, original.key_prefix) is None
    assert await _receiver_sees(db, replacement_prefix) is not None


async def test_a_minted_key_lands_in_the_callers_own_project_only(db: AsyncSession) -> None:
    """The isolation invariant on a write, held by the row rather than by care."""
    tenant = await _new_tenant(db)
    stranger = await _new_tenant(db)

    created = await apikeys.create(
        db,
        org_id=tenant.organization.id,
        actor=tenant.user,
        project_id=tenant.project.id,
        name="exporter",
    )
    await db.flush()

    row = await _key_by_prefix(db, _plaintext(created)[:API_KEY_PREFIX_LENGTH])
    assert row.org_id == tenant.organization.id
    assert row.project_id == tenant.project.id

    listing = await apikeys.list_for_project(
        db, org_id=stranger.organization.id, project_id=stranger.project.id
    )
    assert created.api_key.id not in [summary.id for summary in listing.api_keys]


async def test_the_database_stores_no_form_of_the_plaintext_key(db: AsyncSession) -> None:
    """Read back over every column of the row, from the database, not the object."""
    tenant = await _new_tenant(db)

    created = await apikeys.create(
        db,
        org_id=tenant.organization.id,
        actor=tenant.user,
        project_id=tenant.project.id,
        name="exporter",
    )
    await db.flush()

    stored = await db.execute(
        text("SELECT api_keys::text FROM api_keys WHERE key_prefix = :key_prefix"),
        {"key_prefix": _plaintext(created)[:API_KEY_PREFIX_LENGTH]},
    )
    row_text = stored.scalar_one()
    assert _plaintext(created) not in row_text
    # The secret half in particular: the prefix is stored in the clear and shares
    # its first sixteen characters with the key.
    assert _plaintext(created)[API_KEY_PREFIX_LENGTH:] not in row_text


async def test_two_keys_in_one_project_never_share_a_prefix(db: AsyncSession) -> None:
    """`uq_api_keys_key_prefix` is what the receiver's lookup depends on."""
    tenant = await _new_tenant(db)

    minted = [
        await apikeys.create(
            db,
            org_id=tenant.organization.id,
            actor=tenant.user,
            project_id=tenant.project.id,
            name=f"key {index}",
        )
        for index in range(5)
    ]
    await db.flush()

    prefixes = {created.api_key.key_hint for created in minted}
    listing = await apikeys.list_for_project(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    # Five minted plus the one signup created.
    assert len(listing.api_keys) == 6
    assert len(prefixes) == len(minted)


async def test_creating_a_key_records_who_minted_it(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)

    created = await apikeys.create(
        db,
        org_id=tenant.organization.id,
        actor=tenant.user,
        project_id=tenant.project.id,
        name="exporter",
    )
    await db.flush()

    recorded = [
        entry
        for entry in await _entries(db, tenant.organization.id)
        if entry.action == audit.API_KEY_CREATED
    ]
    # One from signup, one from this mint.
    assert len(recorded) == 2
    assert recorded[-1].target == f"{audit.API_KEY_TARGET}:{created.api_key.id}"
    assert _plaintext(created) not in " ".join(entry.target for entry in recorded)


# ------------------------------------------------------------- the last use


async def test_the_receivers_write_moves_last_used_forward(db: AsyncSession) -> None:
    """What turns "has the old key gone quiet?" from a guess into a reading."""
    tenant = await _new_tenant(db)
    key = await _only_key(db, tenant.organization.id)
    assert key.last_used_at is None, "a key nothing has sent with has no last use"

    used_at = datetime.now(UTC)
    await _receiver_marks_used(db, key.key_prefix, used_at)

    assert (await _key_by_prefix(db, key.key_prefix)).last_used_at == used_at


async def test_an_out_of_order_write_never_moves_last_used_backwards(db: AsyncSession) -> None:
    """Several receivers each hold their own cache, so writes arrive unordered."""
    tenant = await _new_tenant(db)
    key = await _only_key(db, tenant.organization.id)

    later = datetime.now(UTC)
    earlier = later - timedelta(minutes=5)

    await _receiver_marks_used(db, key.key_prefix, later)
    await _receiver_marks_used(db, key.key_prefix, earlier)

    assert (await _key_by_prefix(db, key.key_prefix)).last_used_at == later


async def test_a_revoked_key_is_never_stamped_as_used(db: AsyncSession) -> None:
    """A withdrawn key is not in use, and a stamp on one would read as still live."""
    tenant = await _new_tenant(db)
    key = await _only_key(db, tenant.organization.id)

    await apikeys.revoke(db, org_id=tenant.organization.id, actor=tenant.user, key_id=key.id)
    await db.flush()
    await _receiver_marks_used(db, key.key_prefix, datetime.now(UTC))

    assert (await _key_by_prefix(db, key.key_prefix)).last_used_at is None


async def test_the_console_reads_back_the_last_use_the_receiver_wrote(db: AsyncSession) -> None:
    """The two ends of the feature: the receiver's write, the console's read."""
    tenant = await _new_tenant(db)
    key = await _only_key(db, tenant.organization.id)

    used_at = datetime.now(UTC)
    await _receiver_marks_used(db, key.key_prefix, used_at)
    await db.refresh(key)

    listing = await apikeys.list_for_project(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )

    (summary,) = [row for row in listing.api_keys if row.id == key.id]
    assert summary.last_used_at == used_at


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

    assert tenant.api_key.get_secret_value() not in recorded
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

    await apikeys.revoke(db, org_id=tenant.organization.id, actor=tenant.user, key_id=key.id)
    await db.flush()

    assert await _receiver_sees(db, key.key_prefix) is None


async def test_revoking_records_who_did_it_in_the_same_transaction(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    key = await _only_key(db, tenant.organization.id)

    await apikeys.revoke(db, org_id=tenant.organization.id, actor=tenant.user, key_id=key.id)
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
            actor=attacker.user,
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
        db, org_id=tenant.organization.id, actor=tenant.user, key_id=key.id
    )
    second = await apikeys.revoke(
        db, org_id=tenant.organization.id, actor=tenant.user, key_id=key.id
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

    await apikeys.revoke(db, org_id=tenant.organization.id, actor=tenant.user, key_id=key.id)
    listing = await apikeys.list_for_project(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )

    assert [summary.id for summary in listing.api_keys] == [key.id]
    assert listing.api_keys[0].revoked_at is not None

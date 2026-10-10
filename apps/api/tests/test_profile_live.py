"""Profile and security against real Postgres (P1-72).

The offline suite proves the rules. This one proves the **stores agree**, and in
particular the one claim that cannot be shown offline: after a sign-out, a
session created before it must stop resolving. `_issued_before_reset` is a
comparison, and a comparison passing against a hand-built object says nothing
about whether the column it reads is on the row a real request loads. Here the
identity is resolved the way a request resolves it — through `load_identity` —
against a row a real change wrote.

Everything runs inside one transaction that is rolled back, so the suite leaves
the development database as it found it; the session store's transactions are
savepoints on that connection.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nexovue_api.auth.credentials import verify_password
from nexovue_api.auth.dependencies import _issued_before_reset
from nexovue_api.auth.queries import load_identity
from nexovue_api.auth.sessions import Session, SessionStore
from nexovue_api.data.postgres.models import User
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.profile import service as profile
from nexovue_api.profile.schemas import ProfileUpdate
from nexovue_api.signup import service as signup
from nexovue_api.signup.service import ProvisionedTenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

# Long enough that nothing here races it, short enough that a crashed run leaves
# nothing behind for more than a minute.
TEST_TTL_SECONDS: Final = 60

OLD_PASSWORD: Final = "the-old-one"
NEW_PASSWORD: Final = "the-new-one"


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or default_port),
            REACHABILITY_TIMEOUT_SECONDS,
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN, 5432),
    suite="the live profile-and-security suite",
    remedy="Postgres is not running — start it with `just dev`",
)


@pytest.fixture
async def connection() -> AsyncIterator[AsyncConnection]:
    """One connection in a transaction that is always rolled back."""
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as held:
            transaction = await held.begin()
            try:
                yield held
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def db(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    session = AsyncSession(bind=connection)
    try:
        yield session
    finally:
        await session.close()


@pytest.fixture
def sessions(connection: AsyncConnection) -> SessionStore:
    return SessionStore(
        async_sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ),
        ttl_seconds=TEST_TTL_SECONDS,
    )


async def _tenant(db: AsyncSession, *, password: str = OLD_PASSWORD) -> ProvisionedTenant:
    """A real tenant, made the way signup makes one."""
    suffix = uuid.uuid4().hex[:12]
    return await signup.provision_tenant(
        db,
        organization_name=f"Profile Suite {suffix}",
        email=f"profile-{suffix}@nexovue.test",
        password=password,
        name=None,
    )


async def _reloaded(db: AsyncSession, user: User) -> User:
    """The user row as an authenticated request would load it."""
    await db.flush()
    resolved = await load_identity(db, user_id=user.id, org_id=user.org_id)
    assert resolved is not None, "the tenant vanished between the write and the read"
    return resolved[0]


def _session_created_at(user: User, moment: datetime) -> Session:
    return Session(user_id=str(user.id), org_id=str(user.org_id), created_at=moment)


async def test_signing_out_everywhere_stops_an_older_session_resolving(
    db: AsyncSession, sessions: SessionStore
) -> None:
    """**The one that must be proved live.**

    A session minted before the call must stop resolving afterwards, judged the
    way `auth.dependencies` judges it: against the row `load_identity` returns
    from Postgres, not against an object this test built.
    """
    tenant = await _tenant(db)
    user = tenant.user
    token, before = await sessions.create(user_id=str(user.id), org_id=str(user.org_id))
    assert await sessions.get(token) is not None, "the store did not mint a session"

    await profile.sign_out_everywhere(db, sessions, user=user, current_token=token)
    reloaded = await _reloaded(db, user)

    assert _issued_before_reset(before, reloaded), (
        "a session that predates the sign-out still resolves; the customer asked "
        "to be signed out everywhere and was not"
    )
    assert await sessions.get(token) is None, "the caller's own token survived"
    after = _session_created_at(user, datetime.now(UTC) + timedelta(seconds=1))
    assert not _issued_before_reset(after, reloaded), "signing in again would be refused"


async def test_a_password_change_keeps_this_session_and_ends_the_others(
    db: AsyncSession, sessions: SessionStore
) -> None:
    """The asymmetry with a reset, proved against real stores: the token handed
    back resolves, and every session that existed before it does not."""
    tenant = await _tenant(db)
    user = tenant.user
    old_token, old_session = await sessions.create(user_id=str(user.id), org_id=str(user.org_id))

    new_token = await profile.change_password(
        db,
        sessions,
        user=user,
        current_token=old_token,
        current_password=OLD_PASSWORD,
        new_password=NEW_PASSWORD,
    )
    reloaded = await _reloaded(db, user)

    assert verify_password(reloaded.password_hash, NEW_PASSWORD)
    assert not verify_password(reloaded.password_hash, OLD_PASSWORD)
    assert _issued_before_reset(old_session, reloaded), "an older session still resolves"
    assert await sessions.get(old_token) is None, "the old token survived"

    minted = await sessions.get(new_token)
    assert minted is not None, "the replacement session was not stored"
    assert not _issued_before_reset(minted, reloaded), (
        "the session handed back is refused by the epoch the same call wrote; "
        "the customer would be signed out of the device they just used"
    )


async def test_a_wrong_current_password_changes_nothing_in_the_row(
    db: AsyncSession, sessions: SessionStore
) -> None:
    """Refused *before* anything is written: neither the hash nor the revocation
    epoch may move, or a stolen session could sign the owner out by guessing."""
    tenant = await _tenant(db)
    user = tenant.user
    token, before = await sessions.create(user_id=str(user.id), org_id=str(user.org_id))

    with pytest.raises(InvalidRequestError, match="not the current password"):
        await profile.change_password(
            db,
            sessions,
            user=user,
            current_token=token,
            current_password="not-the-old-one",
            new_password=NEW_PASSWORD,
        )
    reloaded = await _reloaded(db, user)

    assert verify_password(reloaded.password_hash, OLD_PASSWORD)
    assert reloaded.sessions_valid_from is None, "a refused change ended every session"
    assert not _issued_before_reset(before, reloaded)
    assert await sessions.get(token) is not None, "a refused change revoked the caller's session"


async def test_a_name_is_written_and_cleared_on_the_real_row(db: AsyncSession) -> None:
    """`users.name` is nullable, so "cleared" has to reach Postgres as NULL and
    not as an empty string."""
    tenant = await _tenant(db)
    user = tenant.user

    profile.update_details(user, ProfileUpdate(name="  Ada Lovelace  ", timezone=None))
    assert (await _reloaded(db, user)).name == "Ada Lovelace"

    profile.update_details(user, ProfileUpdate(name="   ", timezone=None))
    assert (await _reloaded(db, user)).name is None


async def test_a_timezone_is_written_and_cleared_on_the_real_row(db: AsyncSession) -> None:
    """Migration 0021's column, proved against the database that has it.

    `users.timezone` is nullable and NULL means "no zone chosen, render in the
    browser's", so clearing has to reach Postgres as NULL rather than as `""`.
    """
    tenant = await _tenant(db)
    user = tenant.user
    assert (await _reloaded(db, user)).timezone is None, "a new account arrives with a zone set"

    profile.update_details(user, ProfileUpdate(name=None, timezone=" Asia/Kolkata "))
    assert (await _reloaded(db, user)).timezone == "Asia/Kolkata"

    profile.update_details(user, ProfileUpdate(name=None, timezone=""))
    assert (await _reloaded(db, user)).timezone is None


async def test_a_zone_the_system_cannot_render_never_reaches_the_column(
    db: AsyncSession,
) -> None:
    """An offset is the tempting wrong value and it is refused: `+05:30` is true
    for half of any year that observes daylight saving, and a column of offsets
    is silently wrong for the other half."""
    tenant = await _tenant(db)
    user = tenant.user

    with pytest.raises(InvalidRequestError):
        profile.update_details(user, ProfileUpdate(name=None, timezone="+05:30"))

    assert (await _reloaded(db, user)).timezone is None

"""The platform gate (`docs/changes/super-admin-in-the-console/`), on real Postgres.

The admin surface lives on the customer origin now, so a session that reaches
the console must not reach the platform by itself. What stands between them is
this: the operator's role, *and* a re-authentication no older than thirty
minutes, stamped on the session itself so signing out revokes it.

What it proves: nobody but the operator gets in, whatever they present; a wrong
password is refused and recorded; the stamp expires; it does not outlive the
session; and an owner with a perfectly good password is refused — this endpoint
cannot be used to find out who holds the role.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nexovue_api.auth.sessions import Session, SessionStore
from nexovue_api.auth.totp import SecondFactorStore
from nexovue_api.data.postgres.models import SUPER_ADMIN_ROLE, User
from nexovue_api.errors.exceptions import PermissionDeniedError, UnauthenticatedError
from nexovue_api.signup import service as signup
from nexovue_api.superadmin import adminmode

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
PASSWORD: Final = "a long operator password"


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_admin_mode_live.py",
    remedy="Postgres is not reachable — run `just up`",
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
    session = AsyncSession(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()


@pytest.fixture
def sessions(connection: AsyncConnection) -> SessionStore:
    """The store's transactions as savepoints on the test's own connection."""
    return SessionStore(
        async_sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ),
        ttl_seconds=600,
    )


async def _person(db: AsyncSession, role: str) -> User:
    """A real person, so a session can name them."""
    suffix = uuid.uuid4().hex[:8]
    person = (
        await signup.provision_tenant(
            db,
            organization_name=f"Admin Mode {suffix}",
            email=f"{role}-{suffix}@example.test",
            password=PASSWORD,
            name="Raj",
        )
    ).user
    person.role = role
    await db.flush()
    return person


class _NoFactors:
    """No second factor enrolled, which is the common case for the operator."""

    async def claim_step(self, *, user_id: str, code: str) -> bool:
        raise AssertionError("a password check must not reach the second factor")


async def _entered(
    db: Any, sessions: SessionStore, operator: User, token: str, **presented: Any
) -> datetime:
    # Cast rather than build a real store: what this suite is about is the
    # password path, and reaching a store for a factor nobody enrolled would only
    # hide the assertion below that says it must not be reached.
    factors = cast(SecondFactorStore, _NoFactors())
    return await adminmode.enter(
        db, sessions, factors, None, user=operator, token=token, **presented
    )


class _Audit:
    """The audit log's session, standing in for Postgres.

    It records what was written *and what had been committed when* — which is
    the whole question for a refusal, because the exception that follows one
    rolls back anything still pending. No row is read back here; that is what
    `test_admin_actions_live` is for.
    """

    rows: list[object]
    committed: list[object]

    def __init__(self) -> None:
        self.rows = []
        self.committed = []

    def add(self, entry: object) -> None:
        self.rows.append(entry)

    async def commit(self) -> None:
        self.committed = list(self.rows)


async def test_the_operator_takes_the_platform_by_proving_it_is_them(
    db: AsyncSession, sessions: SessionStore
) -> None:
    operator = await _person(db, SUPER_ADMIN_ROLE)
    token, _ = await sessions.create(user_id=str(operator.id), org_id=str(operator.org_id))

    until = await _entered(_Audit(), sessions, operator, token, password=PASSWORD, code=None)

    stamped = await sessions.peek(token)
    assert stamped is not None
    assert stamped.admin_until == until
    assert stamped.in_admin_mode(datetime.now(UTC))
    adminmode.holds_the_platform(operator, stamped)


async def test_a_wrong_password_takes_nothing(db: AsyncSession, sessions: SessionStore) -> None:
    operator = await _person(db, SUPER_ADMIN_ROLE)
    token, _ = await sessions.create(user_id=str(operator.id), org_id=str(operator.org_id))

    with pytest.raises(PermissionDeniedError) as refused:
        await _entered(_Audit(), sessions, operator, token, password="not it", code=None)

    assert refused.value.detail == adminmode.WRONG_CREDENTIAL
    stamped = await sessions.peek(token)
    assert stamped is not None
    assert stamped.admin_until is None


async def test_with_two_factor_enrolled_the_password_alone_takes_nothing(
    db: AsyncSession, sessions: SessionStore
) -> None:
    """Group 9, A3: the operator who enrolled a second factor proves it with a code.

    Accepting the password as well left the strongest account on the platform
    with a weaker way in than the one it chose.
    """
    operator = await _person(db, SUPER_ADMIN_ROLE)
    operator.totp_secret = b"sealed"
    operator.totp_confirmed_at = datetime.now(UTC)
    await db.flush()
    token, _ = await sessions.create(user_id=str(operator.id), org_id=str(operator.org_id))

    with pytest.raises(PermissionDeniedError) as refused:
        await _entered(_Audit(), sessions, operator, token, password=PASSWORD, code=None)

    assert refused.value.detail == adminmode.CODE_REQUIRED
    stamped = await sessions.peek(token)
    assert stamped is not None
    assert stamped.admin_until is None


async def test_a_refused_attempt_is_written_down_before_the_refusal(
    db: AsyncSession, sessions: SessionStore
) -> None:
    """The refusal is the interesting row, and the 403 would roll it back if it
    were left to the request's own transaction (`auth/service._record_refusal`
    is the same case)."""
    operator = await _person(db, SUPER_ADMIN_ROLE)
    token, _ = await sessions.create(user_id=str(operator.id), org_id=str(operator.org_id))
    log = _Audit()

    with pytest.raises(PermissionDeniedError):
        await _entered(log, sessions, operator, token, password="not it", code=None)

    assert len(log.committed) == 1


@pytest.mark.parametrize("role", ["owner", "admin", "member", "viewer"])
async def test_nobody_else_gets_in_however_good_their_password(
    db: AsyncSession, sessions: SessionStore, role: str
) -> None:
    """Refused for the role, before the password is even looked at — so this
    cannot be used to learn who the operator is."""
    person = await _person(db, role)
    token, _ = await sessions.create(user_id=str(person.id), org_id=str(person.org_id))

    with pytest.raises(PermissionDeniedError) as refused:
        await _entered(_Audit(), sessions, person, token, password=PASSWORD, code=None)

    assert refused.value.detail == adminmode.NOT_THE_OPERATOR


async def test_the_stamp_lapses_and_the_gate_closes_again(db: AsyncSession) -> None:
    operator = await _person(db, SUPER_ADMIN_ROLE)
    now = datetime.now(UTC)
    session = Session(
        user_id=str(operator.id),
        org_id=str(operator.org_id),
        created_at=now,
        admin_until=now + timedelta(minutes=adminmode.ADMIN_MODE_MINUTES),
    )

    adminmode.holds_the_platform(operator, session, now)

    with pytest.raises(PermissionDeniedError) as closed:
        adminmode.holds_the_platform(
            operator, session, now + timedelta(minutes=adminmode.ADMIN_MODE_MINUTES, seconds=1)
        )
    assert closed.value.detail == adminmode.NEEDS_RE_AUTHENTICATION


async def test_stepping_out_ends_it_immediately(db: AsyncSession, sessions: SessionStore) -> None:
    operator = await _person(db, SUPER_ADMIN_ROLE)
    token, _ = await sessions.create(user_id=str(operator.id), org_id=str(operator.org_id))
    await _entered(_Audit(), sessions, operator, token, password=PASSWORD, code=None)

    await adminmode.leave(sessions, token=token)

    stamped = await sessions.peek(token)
    assert stamped is not None
    assert stamped.admin_until is None


async def test_the_platform_cannot_outlive_the_session(
    db: AsyncSession, sessions: SessionStore
) -> None:
    """The stamp is on the session record, so revoking one revokes both. A
    marker held anywhere else could disagree with it."""
    operator = await _person(db, SUPER_ADMIN_ROLE)
    token, _ = await sessions.create(user_id=str(operator.id), org_id=str(operator.org_id))
    await _entered(_Audit(), sessions, operator, token, password=PASSWORD, code=None)

    await sessions.revoke(token)

    assert await sessions.peek(token) is None
    with pytest.raises(UnauthenticatedError):
        await _entered(_Audit(), sessions, operator, token, password=PASSWORD, code=None)

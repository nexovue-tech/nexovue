"""An account under guessing is locked for a while, never for good (readiness group 9, A1).

Before this, sign-in was limited per address and per email, ten a minute each —
about 14,400 guesses a day against one account from addresses that rotate.
Now failed sign-ins are counted on the account: after five in a row it refuses
for a minute, doubling with each further failure to a fifteen-minute ceiling,
and a full sign-in starts it again. Temporary on purpose: a hard lock would let
anyone lock anyone out.

A locked account answers exactly as a wrong password does, so the lock tells a
guesser nothing. Attempts while locked are refused without lengthening it.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from nexovue_api.auth import lockout, service
from nexovue_api.errors.exceptions import UnauthenticatedError
from nexovue_api.signup.service import provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
PASSWORD: Final = "correct-horse-battery"


def _reachable() -> bool:
    parts = urlsplit(POSTGRES_DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_auth_lockout_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    created = create_async_engine(ASYNC_DSN)
    try:
        yield created
    finally:
        await created.dispose()


@pytest.fixture
async def email(engine: AsyncEngine) -> AsyncIterator[str]:
    address = f"lockout-{uuid.uuid4().hex[:10]}@example.test"
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        made = await provision_tenant(
            session, organization_name="Lockout Suite", email=address, password=PASSWORD
        )
        org = made.organization.id
    try:
        yield address
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": org})


async def _sign_in(engine: AsyncEngine, address: str, password: str) -> bool:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            await service.authenticate(session, email=address, password=password)
        except UnauthenticatedError as refused:
            assert str(refused) == service.INVALID_CREDENTIALS_DETAIL
            return False
        await session.commit()
        return True


async def _lock_of(engine: AsyncEngine, address: str) -> tuple[int, datetime | None]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT failed_logins, locked_until FROM users WHERE email = :email"),
                {"email": address},
            )
        ).one()
    return int(row.failed_logins), row.locked_until


async def _let_the_lock_pass(engine: AsyncEngine, address: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE users SET locked_until = now() - interval '1 second' WHERE email = :e"),
            {"e": address},
        )


async def test_the_sixth_failure_in_a_row_is_refused_even_with_the_right_password(
    engine: AsyncEngine, email: str
) -> None:
    for _ in range(lockout.FAILURES_BEFORE_LOCK - 1):
        assert not await _sign_in(engine, email, "wrong")
    assert await _lock_of(engine, email) == (lockout.FAILURES_BEFORE_LOCK - 1, None)

    assert not await _sign_in(engine, email, "wrong")
    failures, locked_until = await _lock_of(engine, email)
    assert failures == lockout.FAILURES_BEFORE_LOCK
    assert locked_until is not None
    assert timedelta(seconds=50) < locked_until - datetime.now(UTC) <= lockout.FIRST_LOCK

    assert not await _sign_in(engine, email, PASSWORD)


async def test_an_attempt_while_locked_does_not_lengthen_the_lock(
    engine: AsyncEngine, email: str
) -> None:
    for _ in range(lockout.FAILURES_BEFORE_LOCK):
        await _sign_in(engine, email, "wrong")
    before = await _lock_of(engine, email)

    await _sign_in(engine, email, "wrong")

    assert await _lock_of(engine, email) == before


async def test_each_failure_after_a_lock_doubles_it_up_to_the_ceiling(
    engine: AsyncEngine, email: str
) -> None:
    for _ in range(lockout.FAILURES_BEFORE_LOCK):
        await _sign_in(engine, email, "wrong")
    lengths = []
    for _ in range(6):
        await _let_the_lock_pass(engine, email)
        await _sign_in(engine, email, "wrong")
        _, locked_until = await _lock_of(engine, email)
        assert locked_until is not None
        lengths.append(round((locked_until - datetime.now(UTC)).total_seconds() / 60))

    assert lengths == [2, 4, 8, 15, 15, 15]


async def test_a_full_sign_in_starts_the_count_again(engine: AsyncEngine, email: str) -> None:
    for _ in range(lockout.FAILURES_BEFORE_LOCK - 1):
        await _sign_in(engine, email, "wrong")

    assert await _sign_in(engine, email, PASSWORD)

    assert await _lock_of(engine, email) == (0, None)


async def test_an_unknown_email_locks_nothing_and_answers_the_same() -> None:
    address = f"nobody-{uuid.uuid4().hex[:10]}@example.test"
    engine = create_async_engine(ASYNC_DSN)
    try:
        for _ in range(lockout.FAILURES_BEFORE_LOCK + 1):
            assert not await _sign_in(engine, address, "wrong")
    finally:
        await engine.dispose()

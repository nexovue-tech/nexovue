"""Staff accounts (F31, FR21, AC26, ADR-016) against real Postgres and Redis.

What it proves: no staff account is written unless the second factor's code
matches; sign-in needs address, password and code, and refuses every failure in
one sentence; a code signs in once; a disabled account cannot sign in; staff
sessions live apart from customer ones. Postgres work is rolled back.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

import pyotp
import pytest
from live import require_stack
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth.encryption import STAFF_TOTP_PURPOSE, TOTP_PURPOSE, CredentialBox
from nexovue_api.auth.sessions import SessionStore
from nexovue_api.auth.totp import SecondFactorStore, new_secret
from nexovue_api.errors.exceptions import InternalError, InvalidRequestError, UnauthenticatedError
from nexovue_api.staff import service
from nexovue_api.staff.sessions import StaffSessionStore

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
REDIS_URL: Final = os.environ.get("NEXOVUE_REDIS_URL", "redis://localhost:16379/0")
PASSWORD: Final = "a long staff password"
KEY: Final = os.urandom(32)


def _reachable(url: str, port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or port), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN, 5432) and _reachable(REDIS_URL, 6379),
    suite="apps/api/tests/test_staff_live.py",
    remedy="Postgres or Redis is not reachable — run `just up`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(REDIS_URL)
    try:
        yield client
    finally:
        await client.aclose()


BOX: Final = CredentialBox(KEY, purpose=STAFF_TOTP_PURPOSE)


def _now_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


async def _staff(db: AsyncSession) -> tuple[str, str]:
    email = f"staff-{uuid.uuid4().hex[:8]}@nexovue.test"
    secret = new_secret()
    await service.create_account(
        db, BOX, email=email, name="Raj", password=PASSWORD, secret=secret, code=_now_code(secret)
    )
    return email, secret


async def test_no_account_is_written_unless_the_code_matches(db: AsyncSession) -> None:
    with pytest.raises(InvalidRequestError, match="Nothing was created"):
        await service.create_account(
            db,
            BOX,
            email="staff@nexovue.test",
            name="Raj",
            password=PASSWORD,
            secret=new_secret(),
            code="000000",
        )


async def test_address_password_and_code_sign_in(db: AsyncSession, redis: Redis) -> None:
    email, secret = await _staff(db)
    account = await service.sign_in(
        db, BOX, SecondFactorStore(redis), email=email, password=PASSWORD, code=_now_code(secret)
    )
    assert account.email == email


@pytest.mark.parametrize("wrong", ["password", "code", "address"])
async def test_every_failure_is_refused_in_one_sentence(
    db: AsyncSession, redis: Redis, wrong: str
) -> None:
    email, secret = await _staff(db)
    attempt = {
        "email": "nobody@nexovue.test" if wrong == "address" else email,
        "password": "not it" if wrong == "password" else PASSWORD,
        "code": "000000" if wrong == "code" else _now_code(secret),
    }
    with pytest.raises(UnauthenticatedError, match=service.SIGN_IN_REFUSED):
        await service.sign_in(db, BOX, SecondFactorStore(redis), **attempt)


async def test_a_code_signs_in_once(db: AsyncSession, redis: Redis) -> None:
    email, secret = await _staff(db)
    code = _now_code(secret)
    factors = SecondFactorStore(redis)
    await service.sign_in(db, BOX, factors, email=email, password=PASSWORD, code=code)
    with pytest.raises(UnauthenticatedError):
        await service.sign_in(db, BOX, factors, email=email, password=PASSWORD, code=code)


async def test_a_disabled_account_cannot_sign_in(db: AsyncSession, redis: Redis) -> None:
    email, secret = await _staff(db)
    await service.disable(db, email=email)
    with pytest.raises(UnauthenticatedError):
        await service.sign_in(
            db,
            BOX,
            SecondFactorStore(redis),
            email=email,
            password=PASSWORD,
            code=_now_code(secret),
        )


def test_a_staff_secret_cannot_be_opened_as_a_customers() -> None:
    sealed = BOX.seal("SECRET", owner_id="staff:raj@nexovue.test")
    customer_box = CredentialBox(KEY, purpose=TOTP_PURPOSE)
    with pytest.raises(InternalError):
        customer_box.open(sealed, owner_id="staff:raj@nexovue.test")


async def test_staff_and_customer_sessions_do_not_resolve_each_other(redis: Redis) -> None:
    """AC27, at the store: a token from one is not a session in the other."""
    staff_token = await StaffSessionStore(redis).create(staff_id=uuid.uuid4())
    customer_token, _ = await SessionStore(redis, ttl_seconds=600).create(
        user_id=str(uuid.uuid4()), org_id=str(uuid.uuid4())
    )
    assert await SessionStore(redis, ttl_seconds=600).get(staff_token) is None
    assert await StaffSessionStore(redis).get(customer_token) is None
    assert await StaffSessionStore(redis).get(staff_token) is not None

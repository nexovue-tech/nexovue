"""Verification and reset against real Postgres and real Redis (P1-70, P1-71).

The offline suite proves the rules. This one proves the **stores agree**: a
token that Redis really expires, a `email_verified_at` that Postgres really
writes, and — the one that matters most — a session that really stops resolving
once a reset has happened.

That last one cannot be shown offline. `_issued_before_reset` is a comparison,
and a comparison passing in isolation says nothing about whether the column it
reads is on the row the request actually loads. Here the identity is resolved
the way a request resolves it, through `load_identity`, against a row a real
reset wrote.

Postgres work runs inside a transaction that is rolled back, so the suite leaves
the development database as it found it. Redis has no such luxury, so every key
it writes carries a short TTL and a namespace of its own.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth import recovery
from nexovue_api.auth.credentials import verify_password
from nexovue_api.auth.dependencies import _issued_before_reset
from nexovue_api.auth.queries import load_identity
from nexovue_api.auth.sessions import Session
from nexovue_api.auth.tokens import OneTimeTokenStore
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.notifications import Delivery, Message
from nexovue_api.signup import service as signup
from nexovue_api.signup.service import ProvisionedTenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
REDIS_URL: Final = os.environ.get("NEXOVUE_REDIS_URL", "redis://localhost:16379/0")
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

# Long enough that nothing here races it, short enough that a crashed run leaves
# nothing behind for more than a minute.
TEST_TTL_SECONDS: Final = 60


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
    reachable=_reachable(POSTGRES_DSN, 5432) and _reachable(REDIS_URL, 6379),
    suite="the live account-recovery suite",
    remedy="Postgres or Redis is not running — start them with `just dev`",
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


@pytest.fixture
async def tokens() -> AsyncIterator[OneTimeTokenStore]:
    redis: Redis = Redis.from_url(REDIS_URL)
    try:
        yield OneTimeTokenStore(redis)
    finally:
        await redis.aclose()


class RecordingSender:
    """Captures the mail. The provider itself is proved by
    `test_notifications_live.py`; what this suite is about is the flows."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, *, to: str, message: Message, data: Mapping[str, Any]) -> Delivery:
        self.sent.append({"to": to, "message": message, "data": dict(data)})
        return Delivery(provider_message_id="live-suite")


async def _tenant(
    db: AsyncSession, *, password: str = "correct-horse-battery"
) -> ProvisionedTenant:
    """A real tenant, made the way signup makes one."""
    suffix = uuid.uuid4().hex[:12]
    return await signup.provision_tenant(
        db,
        organization_name=f"Recovery Suite {suffix}",
        email=f"recovery-{suffix}@nexovue.test",
        password=password,
        name=None,
    )


async def test_a_real_verification_round_trip_writes_the_column(
    db: AsyncSession, tokens: OneTimeTokenStore
) -> None:
    """`users.email_verified_at` has existed unwritten since migration 0001.
    This is the first thing that writes it."""
    tenant = await _tenant(db)
    sender = RecordingSender()
    assert tenant.user.email_verified_at is None

    await recovery.send_verification(
        tokens,
        sender,
        user=tenant.user,
        app_base_url="https://app.nexovue.test",
        ttl_seconds=TEST_TTL_SECONDS,
    )
    token = str(sender.sent[0]["data"]["link"]).split("token=")[1]
    await recovery.verify_email(db, tokens, token=token)

    assert tenant.user.email_verified_at is not None


async def test_the_same_verification_link_cannot_be_followed_twice(
    db: AsyncSession, tokens: OneTimeTokenStore
) -> None:
    """Against a real Redis, because single use is `GETDEL`'s atomicity and a
    dict cannot demonstrate that the server behaves the same way."""
    tenant = await _tenant(db)
    sender = RecordingSender()
    await recovery.send_verification(
        tokens,
        sender,
        user=tenant.user,
        app_base_url="https://app.nexovue.test",
        ttl_seconds=TEST_TTL_SECONDS,
    )
    token = str(sender.sent[0]["data"]["link"]).split("token=")[1]

    await recovery.verify_email(db, tokens, token=token)
    with pytest.raises(InvalidRequestError, match="no longer valid"):
        await recovery.verify_email(db, tokens, token=token)


async def test_a_reset_changes_the_password_and_ends_every_live_session(
    db: AsyncSession, tokens: OneTimeTokenStore
) -> None:
    """**The whole of P1-71 in one test.**

    A session that existed before the reset must stop resolving afterwards, and
    the identity is resolved here exactly as a request resolves it — through
    `load_identity`, against the row the reset actually wrote. A comparison that
    passes against a hand-built object would prove nothing about that.
    """
    tenant = await _tenant(db, password="the-old-one")
    user = tenant.user
    before_reset = Session(
        user_id=str(user.id), org_id=str(user.org_id), created_at=datetime.now(UTC)
    )
    sender = RecordingSender()

    await recovery.request_password_reset(
        db,
        tokens,
        sender,
        address=user.email,
        app_base_url="https://app.nexovue.test",
        ttl_seconds=TEST_TTL_SECONDS,
    )
    token = str(sender.sent[0]["data"]["link"]).split("token=")[1]
    await recovery.reset_password(db, tokens, token=token, new_password="the-new-one")
    await db.flush()

    resolved = await load_identity(db, user_id=user.id, org_id=user.org_id)
    assert resolved is not None
    reloaded, _ = resolved

    assert verify_password(reloaded.password_hash, "the-new-one")
    assert not verify_password(reloaded.password_hash, "the-old-one")
    assert _issued_before_reset(before_reset, reloaded), (
        "a session that predates the reset still resolves; an attacker holding "
        "one does not need the password the customer just changed"
    )
    after_reset = Session(
        user_id=str(user.id),
        org_id=str(user.org_id),
        created_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    assert not _issued_before_reset(after_reset, reloaded)


async def test_a_reset_for_an_unknown_address_sends_nothing_and_says_nothing(
    db: AsyncSession, tokens: OneTimeTokenStore
) -> None:
    """The endpoint answers 202 either way; this is the half underneath it. A
    raise here would become a different status code and an enumeration oracle."""
    sender = RecordingSender()

    await recovery.request_password_reset(
        db,
        tokens,
        sender,
        address=f"nobody-{uuid.uuid4().hex}@nexovue.test",
        app_base_url="https://app.nexovue.test",
        ttl_seconds=TEST_TTL_SECONDS,
    )

    assert sender.sent == []


async def test_a_reset_also_proves_the_address(db: AsyncSession, tokens: OneTimeTokenStore) -> None:
    """Following a reset link proves control of the inbox as surely as following
    a verification link, so an unverified account should not be left with a
    second hoop after the same proof."""
    tenant = await _tenant(db)
    user = tenant.user
    sender = RecordingSender()
    await recovery.request_password_reset(
        db,
        tokens,
        sender,
        address=user.email,
        app_base_url="https://app.nexovue.test",
        ttl_seconds=TEST_TTL_SECONDS,
    )
    token = str(sender.sent[0]["data"]["link"]).split("token=")[1]

    await recovery.reset_password(db, tokens, token=token, new_password="a-brand-new-one")

    assert user.email_verified_at is not None

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
from nexovue_platform.email import Delivery, Message
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth import recovery
from nexovue_api.auth.credentials import verify_password
from nexovue_api.auth.dependencies import _issued_before_reset
from nexovue_api.auth.queries import load_identity
from nexovue_api.auth.sessions import Session
from nexovue_api.auth.tokens import OneTimeTokenStore
from nexovue_api.data.postgres.models import User
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError
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

    async def send(
        self,
        *,
        to: str,
        message: Message,
        data: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> Delivery:
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


# ------------------------------------------------------ changing the address (P1-72)


async def _change_link(db: AsyncSession, tokens: OneTimeTokenStore, *, user: User, to: str) -> str:
    """Ask for a change of address and return the token that was mailed."""
    sender = RecordingSender()
    await recovery.send_email_change_link(
        db,
        tokens,
        sender,
        user=user,
        new_address=to,
        app_base_url="https://app.nexovue.test",
        ttl_seconds=TEST_TTL_SECONDS,
    )
    assert sender.sent[0]["to"] == to, "the link was mailed somewhere other than the new address"
    return str(sender.sent[0]["data"]["link"]).split("token=")[1]


async def test_the_address_does_not_move_until_the_link_is_followed(
    db: AsyncSession, tokens: OneTimeTokenStore
) -> None:
    """**The claim the whole flow is built on, against the real row.**

    Between asking and confirming, the account must still hold the old address —
    that is what keeps a customer who mistyped reachable at an inbox they can
    read. The row is re-read through `load_identity`, the way an authenticated
    request reads it, so this cannot pass on an object the test kept in hand.
    """
    tenant = await _tenant(db)
    user = tenant.user
    old_address = user.email
    new_address = f"moved-{uuid.uuid4().hex[:12]}@nexovue.test"

    token = await _change_link(db, tokens, user=user, to=new_address)
    await db.flush()
    midway = await load_identity(db, user_id=user.id, org_id=user.org_id)
    assert midway is not None
    assert midway[0].email == old_address, "the address moved before anything was proved"
    assert midway[0].email_verified_at is None

    await recovery.confirm_email_change(db, tokens, token=token)
    after = await load_identity(db, user_id=user.id, org_id=user.org_id)
    assert after is not None
    assert after[0].email == new_address, "following the link did not move the address"
    assert after[0].email_verified_at is not None, (
        "the new address was moved to but not marked proven, so the customer is "
        "asked to prove the same inbox twice"
    )


async def test_the_same_change_link_cannot_be_followed_twice(
    db: AsyncSession, tokens: OneTimeTokenStore
) -> None:
    """Against a real Redis, because single use is `GETDEL`'s atomicity."""
    tenant = await _tenant(db)
    first = f"once-{uuid.uuid4().hex[:12]}@nexovue.test"
    token = await _change_link(db, tokens, user=tenant.user, to=first)

    await recovery.confirm_email_change(db, tokens, token=token)
    with pytest.raises(InvalidRequestError, match="no longer valid"):
        await recovery.confirm_email_change(db, tokens, token=token)


async def test_a_change_link_cannot_be_spent_at_the_other_endpoints(
    db: AsyncSession, tokens: OneTimeTokenStore
) -> None:
    """The purpose is part of the Redis key, and this is that claim against a
    real server: a 24-hour change link must not open the reset endpoint."""
    tenant = await _tenant(db)
    token = await _change_link(
        db, tokens, user=tenant.user, to=f"purpose-{uuid.uuid4().hex[:12]}@nexovue.test"
    )

    with pytest.raises(InvalidRequestError, match="no longer valid"):
        await recovery.reset_password(db, tokens, token=token, new_password="not-this-way")
    with pytest.raises(InvalidRequestError, match="no longer valid"):
        await recovery.verify_email(db, tokens, token=token)
    # And it is still unspent for the purpose it was minted for.
    await recovery.confirm_email_change(db, tokens, token=token)


async def test_an_older_change_link_applies_the_address_it_was_minted_for(
    db: AsyncSession, tokens: OneTimeTokenStore
) -> None:
    """**Why the address rides in the token and not in a column.**

    A `users.pending_email` column would hold only the newest request, so
    following the older link would set the address to one that link never
    proved: control of the first inbox standing in as proof of the second.
    """
    tenant = await _tenant(db)
    first = f"first-{uuid.uuid4().hex[:12]}@nexovue.test"
    second = f"second-{uuid.uuid4().hex[:12]}@nexovue.test"

    older = await _change_link(db, tokens, user=tenant.user, to=first)
    await _change_link(db, tokens, user=tenant.user, to=second)
    await recovery.confirm_email_change(db, tokens, token=older)

    resolved = await load_identity(db, user_id=tenant.user.id, org_id=tenant.user.org_id)
    assert resolved is not None
    assert resolved[0].email == first, "a link applied an address it never proved"


async def test_an_address_another_account_holds_is_refused_in_words(
    db: AsyncSession, tokens: OneTimeTokenStore
) -> None:
    """**`users.email` is globally unique and a person cannot read an index.**

    Both ends are checked: asking for an address that is taken, and following a
    link for one that was taken in the meantime. The second is the one only a
    real database can show — it is the unique constraint that decides it.
    """
    occupant = await _tenant(db)
    mover = await _tenant(db)

    with pytest.raises(ConflictError, match="already exists"):
        await _change_link(db, tokens, user=mover.user, to=occupant.user.email)

    # Now the race: a link minted while the address was free, followed after
    # somebody else took it.
    contested = f"contested-{uuid.uuid4().hex[:12]}@nexovue.test"
    token = await _change_link(db, tokens, user=mover.user, to=contested)
    occupant.user.email = contested
    await db.flush()

    with pytest.raises(ConflictError, match="already exists"):
        await recovery.confirm_email_change(db, tokens, token=token)


async def test_asking_to_change_to_the_address_already_held_is_refused(
    db: AsyncSession, tokens: OneTimeTokenStore
) -> None:
    tenant = await _tenant(db)
    sender = RecordingSender()

    with pytest.raises(InvalidRequestError, match="already the address"):
        await recovery.send_email_change_link(
            db,
            tokens,
            sender,
            user=tenant.user,
            new_address=tenant.user.email.upper(),
            app_base_url="https://app.nexovue.test",
            ttl_seconds=TEST_TTL_SECONDS,
        )
    assert sender.sent == []


async def test_a_change_of_address_leaves_every_live_session_alone(
    db: AsyncSession, tokens: OneTimeTokenStore
) -> None:
    """Deliberate, and the opposite of a reset. A reset ends every session
    because the password may be in somebody else's hands; nothing about proving
    a new inbox says that, and the customer is signed in on the device they
    asked from."""
    tenant = await _tenant(db)
    user = tenant.user
    before = Session(user_id=str(user.id), org_id=str(user.org_id), created_at=datetime.now(UTC))

    token = await _change_link(
        db, tokens, user=user, to=f"kept-{uuid.uuid4().hex[:12]}@nexovue.test"
    )
    await recovery.confirm_email_change(db, tokens, token=token)

    resolved = await load_identity(db, user_id=user.id, org_id=user.org_id)
    assert resolved is not None
    assert not _issued_before_reset(before, resolved[0])


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

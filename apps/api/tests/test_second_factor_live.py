"""The session index and the second factor against the real stores (T64).

The offline suites prove the rules. This one proves the two claims that a double
cannot: **Redis really holds a per-user index that a customer's own list is
built from**, and **Postgres really holds a sealed secret and argon2 recovery
codes, under a constraint that refuses a half-armed account.**

Postgres work runs inside a transaction that is rolled back, so the suite leaves
the development database as it found it. Redis has no such luxury, so every
session it mints carries a short TTL.
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
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth import recovery_codes, second_factor
from nexovue_api.auth.clients import ClientDescription
from nexovue_api.auth.encryption import credential_box, generate_key
from nexovue_api.auth.sessions import SessionStore
from nexovue_api.auth.totp import SecondFactorStore
from nexovue_api.data.postgres.models import User, UserRecoveryCode
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

PASSWORD: Final = "correct-horse-battery"
CHROME_ON_MAC: Final = ClientDescription(label="Chrome on macOS", network="203.0.113.0/24")


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
    suite="the live second-factor and session-list suite",
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
async def redis() -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(REDIS_URL)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def sessions(redis: Redis) -> SessionStore:
    return SessionStore(redis, ttl_seconds=TEST_TTL_SECONDS)


@pytest.fixture
def second_factors(redis: Redis) -> SecondFactorStore:
    return SecondFactorStore(redis)


async def _tenant(db: AsyncSession) -> ProvisionedTenant:
    """A real tenant, made the way signup makes one."""
    suffix = uuid.uuid4().hex[:12]
    return await signup.provision_tenant(
        db,
        organization_name=f"Second Factor Suite {suffix}",
        email=f"second-factor-{suffix}@nexovue.test",
        password=PASSWORD,
        name=None,
    )


# ------------------------------------------------------- the session index


async def test_a_minted_session_appears_in_its_own_users_list(
    db: AsyncSession, sessions: SessionStore
) -> None:
    """The index is written beside the session key, so the list is real."""
    user = (await _tenant(db)).user

    token, _ = await sessions.create(
        user_id=str(user.id), org_id=str(user.org_id), client=CHROME_ON_MAC
    )
    listed = await sessions.list_for(str(user.id))

    assert [entry.client for entry in listed] == [CHROME_ON_MAC]
    assert await sessions.get(token) is not None


async def test_one_users_list_never_holds_anothers_session(
    db: AsyncSession, sessions: SessionStore
) -> None:
    """**The scoping claim against a real store.** The index key is built from
    the user id the caller's own session names, so there is nothing a request
    could supply that reaches another set."""
    mine = (await _tenant(db)).user
    theirs = (await _tenant(db)).user
    await sessions.create(user_id=str(mine.id), org_id=str(mine.org_id))
    await sessions.create(user_id=str(theirs.id), org_id=str(theirs.org_id))

    assert len(await sessions.list_for(str(mine.id))) == 1
    assert len(await sessions.list_for(str(theirs.id))) == 1


async def test_a_session_id_that_is_not_yours_will_not_end_it(
    db: AsyncSession, sessions: SessionStore
) -> None:
    """**Membership is the authorization.** Holding somebody else's id — which
    is only a SHA-256 digest of their token — must buy nothing."""
    mine = (await _tenant(db)).user
    theirs = (await _tenant(db)).user
    their_token, _ = await sessions.create(user_id=str(theirs.id), org_id=str(theirs.org_id))
    (their_entry,) = await sessions.list_for(str(theirs.id))

    refused = await sessions.revoke_one_of(user_id=str(mine.id), session_id=their_entry.id)

    assert refused is False
    assert await sessions.get(their_token) is not None, "another user's session was ended"


async def test_ending_one_session_removes_it_from_the_store_and_the_index(
    db: AsyncSession, sessions: SessionStore
) -> None:
    user = (await _tenant(db)).user
    first, _ = await sessions.create(user_id=str(user.id), org_id=str(user.org_id))
    second, _ = await sessions.create(user_id=str(user.id), org_id=str(user.org_id))
    entries = {entry.id: entry for entry in await sessions.list_for(str(user.id))}
    from nexovue_api.auth.sessions import token_fingerprint

    ended = await sessions.revoke_one_of(user_id=str(user.id), session_id=token_fingerprint(first))

    assert ended is True
    assert await sessions.get(first) is None
    assert await sessions.get(second) is not None
    assert [entry.id for entry in await sessions.list_for(str(user.id))] == [
        token_fingerprint(second)
    ]
    assert len(entries) == 2


async def test_a_session_whose_key_has_gone_drops_out_of_the_list(
    db: AsyncSession, sessions: SessionStore, redis: Redis
) -> None:
    """**The drift migration 0019 warned about, answered.** The key is the truth
    and the index is a cache of it, so a member the key's TTL outran must not be
    shown as a live session."""
    from nexovue_api.auth.sessions import token_fingerprint

    user = (await _tenant(db)).user
    token, _ = await sessions.create(user_id=str(user.id), org_id=str(user.org_id))
    await redis.delete(f"session:{token_fingerprint(token)}")

    assert await sessions.list_for(str(user.id)) == []


async def test_signing_out_of_a_session_the_normal_way_also_clears_the_index(
    db: AsyncSession, sessions: SessionStore
) -> None:
    user = (await _tenant(db)).user
    token, _ = await sessions.create(user_id=str(user.id), org_id=str(user.org_id))

    await sessions.revoke(token)

    assert await sessions.list_for(str(user.id)) == []


async def test_the_store_holds_a_coarse_client_and_not_the_request_that_made_it(
    db: AsyncSession, sessions: SessionStore, redis: Redis
) -> None:
    """**The privacy decision, checked where it would actually leak.** What is in
    Redis is what a dump of Redis discloses."""
    from nexovue_api.auth.sessions import token_fingerprint

    user = (await _tenant(db)).user
    token, _ = await sessions.create(
        user_id=str(user.id), org_id=str(user.org_id), client=CHROME_ON_MAC
    )

    stored = await redis.get(f"session:{token_fingerprint(token)}")

    assert stored is not None
    assert b"Chrome on macOS" in stored
    assert b"203.0.113.0/24" in stored
    assert b"Mozilla" not in stored
    assert b"203.0.113.42" not in stored


# ------------------------------------------------- the second factor in Postgres


async def test_the_stored_secret_is_ciphertext_in_the_column(
    db: AsyncSession, second_factors: SecondFactorStore
) -> None:
    """Read back through SQL rather than through the ORM object we just wrote,
    so what is asserted is what the database holds."""
    user = (await _tenant(db)).user
    box = credential_box(generate_key())

    offer = await second_factor.begin_enrolment(
        second_factors, box, user=user, current_password=PASSWORD
    )
    await second_factor.confirm_enrolment(
        db, second_factors, box, user=user, code=pyotp.TOTP(offer.secret).now()
    )
    await db.flush()

    stored = await db.scalar(text("SELECT totp_secret FROM users WHERE id = :id"), {"id": user.id})
    assert stored is not None
    assert offer.secret.encode() not in bytes(stored)
    assert box.open(bytes(stored), owner_id=str(user.id)) == offer.secret


async def test_the_recovery_codes_in_the_table_are_argon2_and_not_the_codes(
    db: AsyncSession, second_factors: SecondFactorStore
) -> None:
    user = (await _tenant(db)).user
    box = credential_box(generate_key())
    offer = await second_factor.begin_enrolment(
        second_factors, box, user=user, current_password=PASSWORD
    )

    codes = await second_factor.confirm_enrolment(
        db, second_factors, box, user=user, code=pyotp.TOTP(offer.secret).now()
    )
    await db.flush()

    rows = (
        (
            await db.execute(
                text("SELECT code_hash FROM user_recovery_codes WHERE user_id = :id"),
                {"id": user.id},
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == recovery_codes.RECOVERY_CODE_COUNT
    for stored in rows:
        assert str(stored).startswith("$argon2")
        assert not any(code.replace("-", "") in str(stored) for code in codes)


async def test_a_recovery_code_is_spent_against_the_real_table(
    db: AsyncSession, second_factors: SecondFactorStore
) -> None:
    user = (await _tenant(db)).user
    box = credential_box(generate_key())
    offer = await second_factor.begin_enrolment(
        second_factors, box, user=user, current_password=PASSWORD
    )
    codes = await second_factor.confirm_enrolment(
        db, second_factors, box, user=user, code=pyotp.TOTP(offer.secret).now()
    )
    await db.flush()

    assert await recovery_codes.consume(db, user_id=user.id, submitted=codes[0])
    await db.flush()
    assert not await recovery_codes.consume(db, user_id=user.id, submitted=codes[0])
    assert await recovery_codes.unused_count(db, user_id=user.id) == (
        recovery_codes.RECOVERY_CODE_COUNT - 1
    )


async def test_turning_the_factor_off_leaves_no_code_behind(
    db: AsyncSession, second_factors: SecondFactorStore
) -> None:
    user = (await _tenant(db)).user
    box = credential_box(generate_key())
    offer = await second_factor.begin_enrolment(
        second_factors, box, user=user, current_password=PASSWORD
    )
    await second_factor.confirm_enrolment(
        db, second_factors, box, user=user, code=pyotp.TOTP(offer.secret).now()
    )
    await db.flush()

    await second_factor.disable(db, second_factors, user=user, current_password=PASSWORD)
    await db.flush()

    assert (
        await db.scalar(
            text("SELECT count(*) FROM user_recovery_codes WHERE user_id = :id"), {"id": user.id}
        )
        == 0
    )
    assert (
        await db.scalar(text("SELECT totp_secret FROM users WHERE id = :id"), {"id": user.id})
        is None
    )


async def test_the_database_refuses_a_half_armed_account(db: AsyncSession) -> None:
    """**The CHECK migration 0025 adds.** A confirmed factor with no secret, or a
    secret nothing ever confirmed, is a state no code path should produce — and
    the database is where that is guaranteed rather than hoped for."""
    user = (await _tenant(db)).user
    await db.flush()

    with pytest.raises(IntegrityError):
        await db.execute(
            text("UPDATE users SET totp_confirmed_at = now() WHERE id = :id"), {"id": user.id}
        )
        await db.flush()


async def test_recovery_codes_go_when_the_user_does(db: AsyncSession) -> None:
    """The cascade `organizations/tables.py` records, against the real catalog.

    A tenant erasure deletes `users` directly; these rows have to go with them or
    a deleted customer's credentials outlive their account.
    """
    user = (await _tenant(db)).user
    db.add(UserRecoveryCode(user_id=user.id, code_hash="$argon2id$not-a-real-hash"))
    await db.flush()

    await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})

    assert (
        await db.scalar(
            text("SELECT count(*) FROM user_recovery_codes WHERE user_id = :id"), {"id": user.id}
        )
        == 0
    )


def test_the_model_carries_no_tenant_column() -> None:
    """Recorded as an assertion because it is a deliberate departure from the
    denormalization rule: nothing reads these by tenant, and a column no
    statement reads is the false assurance migration 0006 deleted."""
    assert "org_id" not in UserRecoveryCode.__table__.c
    assert "org_id" in User.__table__.c

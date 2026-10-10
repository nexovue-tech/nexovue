"""The second factor against real Postgres (T64).

The offline suites prove the rules. This one proves what a double cannot:
**Postgres really holds a sealed secret and argon2 recovery codes, under a
constraint that refuses a half-armed account**, and the short-lived state around
a sign-in — the attempt budget and the spent code — really holds.

Everything runs inside one transaction that is rolled back, so the suite leaves
the development database as it found it. The store's own transactions become
savepoints on that connection.
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
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nexovue_api.auth import recovery_codes, second_factor
from nexovue_api.auth.encryption import credential_box, generate_key
from nexovue_api.auth.totp import MAX_CHALLENGE_ATTEMPTS, SecondFactorStore
from nexovue_api.data.postgres.models import TotpEnrolment, User, UserRecoveryCode
from nexovue_api.signup import service as signup
from nexovue_api.signup.service import ProvisionedTenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

PASSWORD: Final = "correct-horse-battery"


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
    suite="the live second-factor suite",
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
def second_factors(connection: AsyncConnection) -> SecondFactorStore:
    """The store's transactions as savepoints, so they see — and roll back with —
    the tenant this test made."""
    return SecondFactorStore(
        async_sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
    )


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


# ------------------------------------------- the state around a sign-in


async def test_the_fifth_wrong_code_ends_the_challenge(
    db: AsyncSession, second_factors: SecondFactorStore
) -> None:
    """Five is generous for somebody copying six digits and miserly for somebody
    guessing them."""
    user = (await _tenant(db)).user
    await db.flush()
    token = await second_factors.open_challenge(user_id=str(user.id))

    for spent in range(1, MAX_CHALLENGE_ATTEMPTS):
        await second_factors.record_failed_attempt(token=token)
        challenge = await second_factors.read_challenge(token=token)
        assert challenge is not None
        assert challenge.attempts == spent
    await second_factors.record_failed_attempt(token=token)

    assert await second_factors.read_challenge(token=token) is None


async def test_a_code_is_claimed_once(db: AsyncSession, second_factors: SecondFactorStore) -> None:
    """A code observed over a shoulder must not sign anybody in a second time."""
    user = (await _tenant(db)).user
    await db.flush()

    assert await second_factors.claim_step(user_id=str(user.id), code="123456")
    assert not await second_factors.claim_step(user_id=str(user.id), code="123456")
    assert await second_factors.claim_step(user_id=str(user.id), code="654321")


async def test_a_new_enrolment_replaces_the_one_before_and_a_lapsed_one_is_gone(
    db: AsyncSession, second_factors: SecondFactorStore
) -> None:
    """The QR code on screen now is the only one that may be confirmed."""
    user = (await _tenant(db)).user
    await db.flush()
    await second_factors.offer_enrolment(user_id=str(user.id), sealed_secret=b"first")
    await second_factors.offer_enrolment(user_id=str(user.id), sealed_secret=b"second")

    offer = await second_factors.take_enrolment(user_id=str(user.id))
    assert offer is not None
    assert offer.sealed_secret == b"second"

    await db.execute(
        update(TotpEnrolment)
        .where(TotpEnrolment.user_id == user.id)
        .values(expires_at=text("now() - interval '1 second'"))
    )
    assert await second_factors.take_enrolment(user_id=str(user.id)) is None


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

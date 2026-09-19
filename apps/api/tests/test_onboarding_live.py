"""Onboarding a paying customer (F31, FR22-FR24, AC23-AC25) against real Postgres.

Rolled back per test. What it proves: an onboarded organization has exactly the
rows a signed-up one has; its owner has no password until the link sets one, and
following it signs them in verified; the region and the website payment are
recorded; a payment is recorded once; a resent link kills the old one.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.admin import service as admin
from nexovue_api.admin.schemas import OnboardRequest, WebsitePaymentIn
from nexovue_api.auth import set_password
from nexovue_api.data.postgres.models import StaffAccount
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
PASSWORD: Final = "correct horse battery staple"


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_onboarding_live.py",
    remedy="Postgres is not reachable — run `just up`",
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


async def _tenant(db: AsyncSession, name: str) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name=name,
        email=f"{name.lower()}-{uuid.uuid4()}@example.test",
        password=PASSWORD,
    )


def _now() -> datetime:
    return datetime.now(UTC)


async def _staff(db: AsyncSession) -> StaffAccount:
    staff = StaffAccount(
        email=f"staff-{uuid.uuid4().hex[:8]}@nexovue.test",
        name="Raj",
        password_hash="x",
        totp_secret=b"sealed",
    )
    db.add(staff)
    await db.flush()
    return staff


def _request(payment: str | None = None, region: str = "india") -> OnboardRequest:
    return OnboardRequest(
        organization_name="Paying Co",
        owner_email=f"owner-{uuid.uuid4().hex[:8]}@example.test",
        owner_name="Priya",
        region=region,
        payment=WebsitePaymentIn(
            razorpay_payment_id=payment or f"pay_{uuid.uuid4().hex[:14]}",
            amount_minor=49900,
            currency="INR",
        ),
    )


async def _rows_per_table(db: AsyncSession, org_id: uuid.UUID) -> dict[str, int]:
    """Every table with an `org_id`, and how many rows this organization has in it."""
    tables = (
        await db.execute(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE column_name = 'org_id' AND table_schema = 'public' ORDER BY table_name"
            )
        )
    ).scalars()
    counts = {}
    for table in tables:
        counts[table] = (
            await db.execute(
                text(f'SELECT count(*) FROM "{table}" WHERE org_id = :org'), {"org": org_id}
            )
        ).scalar_one()
    return counts


async def test_an_onboarded_organization_has_the_rows_a_signed_up_one_has(
    db: AsyncSession,
) -> None:
    """AC24. The audit log and the payment differ by design: onboarding records
    who onboarded and what was paid."""
    signed_up = await _tenant(db, "SignedUp")
    owner, organization, _ = await admin.onboard(
        db, staff=await _staff(db), request=_request(), now=_now()
    )
    await db.flush()

    ours = await _rows_per_table(db, organization.id)
    theirs = await _rows_per_table(db, signed_up.organization.id)
    by_design = {"audit_log", "website_payments"}
    assert {t: n for t, n in ours.items() if t not in by_design} == {
        t: n for t, n in theirs.items() if t not in by_design
    }
    assert ours["website_payments"] == 1
    assert owner.role == "owner"


async def test_the_owner_sets_their_own_password_through_the_link(db: AsyncSession) -> None:
    """AC23."""
    owner, organization, token = await admin.onboard(
        db, staff=await _staff(db), request=_request(), now=_now()
    )
    had_no_password = owner.password_hash is None

    holder = await set_password.set_password(db, token, name="Priya", password=PASSWORD, now=_now())

    assert had_no_password
    assert holder.organization.id == organization.id
    assert owner.password_hash is not None
    assert owner.email_verified_at is not None


async def test_the_region_is_recorded_and_an_unknown_one_refused(db: AsyncSession) -> None:
    """AC25."""
    _, organization, _ = await admin.onboard(
        db, staff=await _staff(db), request=_request(region="india-south"), now=_now()
    )
    assert organization.region == "india-south"
    with pytest.raises(InvalidRequestError, match="region"):
        await admin.onboard(db, staff=await _staff(db), request=_request(region="mars"), now=_now())


async def test_a_website_payment_is_recorded_once(db: AsyncSession) -> None:
    staff = await _staff(db)
    await admin.onboard(db, staff=staff, request=_request(payment="pay_once"), now=_now())
    with pytest.raises(ConflictError, match="already recorded"):
        await admin.onboard(db, staff=staff, request=_request(payment="pay_once"), now=_now())
    assert (
        await db.scalar(
            text("SELECT count(*) FROM website_payments WHERE razorpay_payment_id = 'pay_once'")
        )
        == 1
    )


async def test_a_resent_owner_link_kills_the_old_one(db: AsyncSession) -> None:
    staff = await _staff(db)
    _, organization, first = await admin.onboard(db, staff=staff, request=_request(), now=_now())
    _, _, second = await admin.new_owner_link(db, staff=staff, org_id=organization.id, now=_now())
    await db.flush()

    with pytest.raises(InvalidRequestError, match="no longer valid"):
        await set_password.holder_of(db, first, now=_now())
    assert (await set_password.holder_of(db, second, now=_now())).organization.id == organization.id


async def test_no_link_is_resent_once_the_owner_has_joined(db: AsyncSession) -> None:
    staff = await _staff(db)
    _, organization, token = await admin.onboard(db, staff=staff, request=_request(), now=_now())
    await set_password.set_password(db, token, name="Priya", password=PASSWORD, now=_now())
    await db.flush()

    with pytest.raises(ConflictError, match="already set a password"):
        await admin.new_owner_link(db, staff=staff, org_id=organization.id, now=_now())

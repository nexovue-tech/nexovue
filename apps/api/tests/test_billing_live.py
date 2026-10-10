"""Checkouts against real Postgres: two tabs take turns (readiness blocker 21).

The unit suite proves the rule with a scripted session. What it cannot prove is
the lock: two checkouts started at the same moment, in two transactions, must
not both see "no live arrangement" and both leave a mandate the customer can
authorise. Here they really run at once.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Final, cast
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexovue_api.billing import CancellationOutcome, Checkout, ConfiguredProcessor, HostedPage
from nexovue_api.billing import service as billing
from nexovue_api.billing.schemas import StartCheckoutRequest
from nexovue_api.data.postgres.models import BillingSubscription, Organization
from nexovue_api.signup import service as signup

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)


def _reachable() -> bool:
    parts = urlsplit(POSTGRES_DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_billing_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


class _Processor:
    """Hands out a new reference per checkout, slowly enough that two overlap."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.ended_now: list[str] = []

    def required_contact_fields(self) -> frozenset[Any]:
        return frozenset()

    async def begin_checkout(self, **_: Any) -> Checkout:
        await asyncio.sleep(0.2)
        reference = f"sub_{uuid.uuid4().hex[:12]}"
        self.started.append(reference)
        return Checkout(
            handoff=HostedPage(url="https://processor.example.test/pay"),
            subscription_ref=reference,
            processor_customer_ref="cust",
        )

    async def cancel(
        self, *, subscription_ref: str, immediately: bool = False
    ) -> CancellationOutcome:
        if immediately:
            self.ended_now.append(subscription_ref)
        return CancellationOutcome.REQUESTED


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def organization(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[Organization]:
    suffix = uuid.uuid4().hex[:10]
    async with factory.begin() as db:
        tenant = await signup.provision_tenant(
            db,
            organization_name=f"Billing Suite {suffix}",
            email=f"billing-{suffix}@nexovue.test",
            password="correct-horse-battery",
            name=None,
        )
    yield tenant.organization
    async with factory.begin() as db:
        await db.execute(delete(Organization).where(Organization.id == tenant.organization.id))


async def test_two_checkouts_at_once_leave_one_that_can_be_authorised(
    factory: async_sessionmaker[AsyncSession], organization: Organization
) -> None:
    processor = _Processor()
    configured = ConfiguredProcessor(
        name="test", processor=cast(Any, processor), processor_currencies=frozenset()
    )

    async def check_out() -> None:
        async with factory.begin() as db:
            await billing.begin_plan_change(
                db,
                configured=configured,
                organization=organization,
                request=StartCheckoutRequest(plan_slug="pro", currency="USD", services_committed=2),
                billing_email="owner@nexovue.test",
                app_base_url="https://app.example.test",
                today=datetime.now(UTC).date(),
            )

    await asyncio.gather(check_out(), check_out())

    async with factory() as db:
        rows = list(
            await db.scalars(
                select(BillingSubscription).where(BillingSubscription.org_id == organization.id)
            )
        )
    statuses = sorted(row.status for row in rows)
    assert statuses == ["cancelled", "pending"], statuses
    [superseded] = [row for row in rows if row.status == "cancelled"]
    assert processor.ended_now == [superseded.subscription_ref]

"""A payment's grace period runs out, against real Postgres (readiness group 3).

The API starts the grace period; this sweep ends it. What must hold: only a
grace that has passed ends, the organization drops to free in the same
statement, and the arrangement is `lapsed` rather than `cancelled` — so a late
payment can still bring it back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.billing import end_lapsed_grace

require_stack(
    reachable=postgres_reachable(),
    suite="the payment grace sweep suite",
    remedy="Postgres is not running — start it with `just dev`",
)


async def _paying(
    engine: AsyncEngine, tenant: AlertTenant, *, grace_until: datetime | None
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE organizations SET plan = 'pro' WHERE id = :org"), {"org": tenant.org_id}
        )
        await connection.execute(
            text(
                """
                INSERT INTO billing_subscriptions
                    (org_id, processor, subscription_ref, plan_slug, status,
                     services_committed, currency, grace_until)
                VALUES (:org, 'test', :ref, 'pro', 'active', 3, 'USD', :grace)
                """
            ),
            {"org": tenant.org_id, "ref": f"sub_{uuid.uuid4().hex[:12]}", "grace": grace_until},
        )


async def _state(engine: AsyncEngine, tenant: AlertTenant) -> tuple[str, str]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT o.plan, s.status FROM organizations o "
                    "JOIN billing_subscriptions s ON s.org_id = o.id WHERE o.id = :org"
                ),
                {"org": tenant.org_id},
            )
        ).one()
    return str(row.plan), str(row.status)


async def test_a_grace_that_has_run_out_drops_the_organization_to_free(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    await _paying(engine, tenant, grace_until=datetime.now(UTC) - timedelta(minutes=1))

    await end_lapsed_grace(engine)

    assert await _state(engine, tenant) == ("free", "lapsed")


@pytest.mark.parametrize("grace", [None, timedelta(days=3)], ids=["paying", "in-grace"])
async def test_a_paying_organization_or_one_still_in_grace_is_left_alone(
    engine: AsyncEngine, tenant: AlertTenant, grace: timedelta | None
) -> None:
    await _paying(engine, tenant, grace_until=None if grace is None else datetime.now(UTC) + grace)

    await end_lapsed_grace(engine)

    assert await _state(engine, tenant) == ("pro", "active")

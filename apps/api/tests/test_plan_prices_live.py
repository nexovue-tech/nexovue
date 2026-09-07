"""The repricing invariant on `plan_prices`, read from the migrated database.

The allowance gate reads exactly one price row — the newest `effective_from`
not after today — and a NULL `browser_share_percent` in it means *no split
recorded*, so both doors spend the one allowance
(`receiver/internal/allowance/postgres.go`, `usage.billing.browser_cap_bytes`).
That reading is correct for every row written before ADR 0097.

The hazard is what comes after. A repricing here is **a new row restating every
column**, never an edit (0015, 0016, ADR 0083) — and nothing in the schema
enforces the "every column" half. A future `free` row that carries the new
price and forgets the share is accepted by Postgres, reverts the tier to the
pooled allowance, and lifts the cap that stops a scraped site key spending the
whole month at the browser door. No constraint, no error, no failing test.

This is that missing test. It is deliberately not "the free tier has a share":
the tiers that carry a cap are about to change (G6 extends it to paid tiers),
so the invariant is stated over **whichever tiers have ever carried one**.

Read-only against the real control plane. The one test that writes does so
inside a transaction that is rolled back.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import Row, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.data.postgres.models import PlanPrice

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

REACHABILITY_TIMEOUT_SECONDS: Final = 1.0


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or 5432), REACHABILITY_TIMEOUT_SECONDS
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_plan_prices_live.py",
    remedy="Postgres is not running — start it with `just dev`",
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


async def rows_that_dropped_a_browser_share(
    session: AsyncSession,
) -> list[Row[tuple[str, date]]]:
    """Every price row that turns a tier's browser cap back off.

    "A tier that has ever carried a cap" is the earliest `effective_from` on
    which that tier recorded a share. From that day onwards the tier is capped,
    so **every** row of that tier from that day on has to carry a share —
    including rows dated in the future, which the gate has not started reading
    yet, and rows dated between two capped ones, which would turn the cap off
    for their window alone.

    Checking only the newest row would pass a repricing the day it is written
    and fail it on the day it takes effect, which is the day it is expensive.
    """
    capped_from = (
        select(
            PlanPrice.plan_slug.label("plan_slug"),
            func.min(PlanPrice.effective_from).label("first_day"),
        )
        .where(PlanPrice.browser_share_percent.is_not(None))
        .group_by(PlanPrice.plan_slug)
        .subquery()
    )
    statement = (
        select(PlanPrice.plan_slug, PlanPrice.effective_from)
        .join(
            capped_from,
            and_(
                capped_from.c.plan_slug == PlanPrice.plan_slug,
                PlanPrice.effective_from >= capped_from.c.first_day,
            ),
        )
        .where(PlanPrice.browser_share_percent.is_(None))
        .order_by(PlanPrice.plan_slug, PlanPrice.effective_from)
    )
    return list((await session.execute(statement)).all())


def _forgotten_column_message(dropped: list[Row[tuple[str, date]]]) -> str:
    """What the person who wrote the next migration needs to read."""
    listed = ", ".join(f"{slug} effective {day}" for slug, day in dropped)
    return (
        f"these plan_prices rows omit browser_share_percent on a tier that already "
        f"carries a browser cap: {listed}. A repricing is a new row restating "
        f"EVERY column (0015, 0016, ADR 0083) — this one restates the price and "
        f"drops the split, which silently returns the tier to the pooled allowance "
        f"and lifts the cap that stops a scraped site key spending the whole month "
        f"at the browser door (ADR 0097). Add browser_share_percent to the row."
    )


async def test_no_tier_that_ever_carried_a_browser_cap_has_a_row_that_drops_it(
    db: AsyncSession,
) -> None:
    """The invariant itself, against whatever the migrations have left behind."""
    dropped = await rows_that_dropped_a_browser_share(db)

    assert dropped == [], _forgotten_column_message(dropped)


async def test_the_guard_catches_a_repricing_that_forgets_the_share(db: AsyncSession) -> None:
    """The guard above is only worth having if it can fail; this is that proof.

    A future `free` row restating the price and omitting the share — the exact
    shape of the next repricing — inserted in a transaction that is rolled
    back. The guard has to name it, and its message has to name the column.
    """
    capped = (
        await db.execute(
            select(PlanPrice)
            .where(PlanPrice.browser_share_percent.is_not(None))
            .order_by(PlanPrice.effective_from.desc())
            .limit(1)
        )
    ).scalar_one()
    repriced_on = capped.effective_from + timedelta(days=365)

    db.add(
        PlanPrice(
            plan_slug=capped.plan_slug,
            effective_from=repriced_on,
            currency=capped.currency,
            price_per_service_minor_units=capped.price_per_service_minor_units,
            max_billed_services=capped.max_billed_services,
            included_bytes_per_service=capped.included_bytes_per_service,
            browser_share_percent=None,
        )
    )
    await db.flush()

    dropped = await rows_that_dropped_a_browser_share(db)

    assert (capped.plan_slug, repriced_on) in [tuple(row) for row in dropped]
    assert "browser_share_percent" in _forgotten_column_message(dropped)

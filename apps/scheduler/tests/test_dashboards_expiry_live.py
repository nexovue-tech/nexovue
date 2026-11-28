"""The expiry sweep's dashboard line (F20 plan T7.6; FR51, AC57), against Postgres.

A dashboard deleted more than 30 days before the sweep's clock goes with every version it
kept; one deleted 29 days before stays, whole, and can still be restored. The clock is
passed in, so 29 and 31 days are exact. The rows are the test's own organization's, and the
sweep, which is organization-blind, is asserted against only those.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.jobs import expiry

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_dashboards_expiry_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

DAY: Final = timedelta(days=1)
# Longer than a test here runs, so no real-clock sweep reaches a row of 30 days or less by
# `clock`; short enough that this test's own sweep reaches no other suite's row.
CLOCK_AHEAD: Final = timedelta(minutes=10)


@pytest.fixture
def clock() -> datetime:
    """This test's own clock, read when it starts, never at import."""
    return datetime.now(UTC) + CLOCK_AHEAD


async def _dashboard(
    engine: AsyncEngine,
    tenant: AlertTenant,
    title: str,
    *,
    deleted: datetime | None,
    clock: datetime,
) -> uuid.UUID:
    """One dashboard with two kept versions, as a save would leave it."""
    async with engine.begin() as connection:
        made: uuid.UUID = await connection.scalar(
            text(
                """
                INSERT INTO dashboards (org_id, title, definition, version, deleted_at)
                VALUES (:org_id, :title, '{"panels": []}', 3, :deleted)
                RETURNING id
                """
            ),
            {"org_id": tenant.org_id, "title": title, "deleted": deleted},
        )
        for version in (1, 2):
            await connection.execute(
                text(
                    """
                    INSERT INTO dashboard_versions
                        (dashboard_id, version, org_id, title, definition, saved_at)
                    VALUES (:id, :version, :org_id, :title, '{"panels": []}', :saved_at)
                    """
                ),
                {
                    "id": made,
                    "version": version,
                    "org_id": tenant.org_id,
                    "title": f"{title} v{version}",
                    "saved_at": clock - 60 * DAY,
                },
            )
    return made


async def _rows(engine: AsyncEngine, statement: str, **keys: Any) -> list[Any]:
    async with engine.connect() as connection:
        return list(await connection.execute(text(statement), keys))


async def test_ac57_a_dashboard_deleted_31_days_ago_goes_with_its_versions_and_29_stays(
    engine: AsyncEngine, tenant: AlertTenant, clock: datetime
) -> None:
    lapsed = await _dashboard(
        engine, tenant, "Deleted 31 days ago", deleted=clock - 31 * DAY, clock=clock
    )
    restorable = await _dashboard(
        engine, tenant, "Deleted 29 days ago", deleted=clock - 29 * DAY, clock=clock
    )
    live = await _dashboard(engine, tenant, "Never deleted", deleted=None, clock=clock)

    await expiry.sweep(engine, now=clock)

    dashboards = await _rows(
        engine, "SELECT id FROM dashboards WHERE org_id = :org_id", org_id=tenant.org_id
    )
    assert {row.id for row in dashboards} == {restorable, live}
    versions = await _rows(
        engine,
        "SELECT dashboard_id FROM dashboard_versions WHERE org_id = :org_id",
        org_id=tenant.org_id,
    )
    assert sorted(str(row.dashboard_id) for row in versions) == sorted(
        [str(restorable)] * 2 + [str(live)] * 2
    )
    assert lapsed not in {row.dashboard_id for row in versions}


async def test_a_dashboard_deleted_exactly_30_days_ago_can_still_be_restored(
    engine: AsyncEngine, tenant: AlertTenant, clock: datetime
) -> None:
    """The API's restore refuses only past `RESTORE_FOR`, so the sweep must leave this one."""
    edge = await _dashboard(engine, tenant, "On the line", deleted=clock - 30 * DAY, clock=clock)

    await expiry.sweep(engine, now=clock)

    kept = await _rows(
        engine, "SELECT id FROM dashboards WHERE org_id = :org_id", org_id=tenant.org_id
    )
    assert [row.id for row in kept] == [edge]


async def test_a_sweep_run_again_removes_nothing_more(
    engine: AsyncEngine, tenant: AlertTenant, clock: datetime
) -> None:
    await _dashboard(engine, tenant, "Long gone", deleted=clock - 90 * DAY, clock=clock)

    await expiry.sweep(engine, now=clock)
    await expiry.sweep(engine, now=clock)

    assert (
        await _rows(
            engine, "SELECT id FROM dashboards WHERE org_id = :org_id", org_id=tenant.org_id
        )
        == []
    )

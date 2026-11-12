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

NOW: Final = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
DAY: Final = timedelta(days=1)


async def _dashboard(
    engine: AsyncEngine, tenant: AlertTenant, title: str, *, deleted: datetime | None
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
                    "saved_at": NOW - 60 * DAY,
                },
            )
    return made


async def _rows(engine: AsyncEngine, statement: str, **keys: Any) -> list[Any]:
    async with engine.connect() as connection:
        return list(await connection.execute(text(statement), keys))


async def test_ac57_a_dashboard_deleted_31_days_ago_goes_with_its_versions_and_29_stays(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    lapsed = await _dashboard(engine, tenant, "Deleted 31 days ago", deleted=NOW - 31 * DAY)
    restorable = await _dashboard(engine, tenant, "Deleted 29 days ago", deleted=NOW - 29 * DAY)
    live = await _dashboard(engine, tenant, "Never deleted", deleted=None)

    removed = await expiry.sweep(engine, now=NOW)

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
    assert removed["dashboards"] >= 1


async def test_a_dashboard_deleted_exactly_30_days_ago_can_still_be_restored(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """The API's restore refuses only past `RESTORE_FOR`, so the sweep must leave this one."""
    edge = await _dashboard(engine, tenant, "On the line", deleted=NOW - 30 * DAY)

    await expiry.sweep(engine, now=NOW)

    kept = await _rows(
        engine, "SELECT id FROM dashboards WHERE org_id = :org_id", org_id=tenant.org_id
    )
    assert [row.id for row in kept] == [edge]


async def test_a_sweep_run_again_removes_nothing_more(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    await _dashboard(engine, tenant, "Long gone", deleted=NOW - 90 * DAY)

    await expiry.sweep(engine, now=NOW)
    await expiry.sweep(engine, now=NOW)

    assert (
        await _rows(
            engine, "SELECT id FROM dashboards WHERE org_id = :org_id", org_id=tenant.org_id
        )
        == []
    )

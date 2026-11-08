"""What reliability targets keep, and for how long (F19 plan T3.4; FR31, D46), against the
development Postgres, on `test_detection_prune_live.py`'s pattern.

The expiry sweep (`sweep:expired-rows`):

- purges a target deleted more than 30 days ago: each month that ended before its
  deletion is written to `slo_deleted_months` first, then the target goes, and its
  status and daily figures with it. Its change history stays (AC71's second half);
- lapses daily figures, changes and purged months at 400 days (AC88's lapsing).

A target deleted less than 30 days ago can still be restored, so it is left alone.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from nexovue_platform import slo
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.jobs import expiry

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_slo_prune_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

DAY: Final = timedelta(days=1)
KEPT: Final = timedelta(days=400)
LONG_AGO: Final = datetime(2020, 1, 1, tzinfo=UTC)


async def _target(
    engine: AsyncEngine, tenant: AlertTenant, *, deleted_ago: timedelta | None, service: str
) -> uuid.UUID:
    deleted = None if deleted_ago is None else datetime.now(UTC) - deleted_ago
    key = slo.definition_key(
        slo.SERVICE_AVAILABILITY, service_name=service, objective=Decimal("99.9"), window_days=28
    )
    async with engine.begin() as connection:
        made: uuid.UUID = await connection.scalar(
            text(
                """
                INSERT INTO slo_targets (org_id, project_id, name, kind, service_name,
                    objective_percent, window_days, upstream_sampling, definition_key,
                    first_event_at, created_at, deleted_at)
                VALUES (:org_id, :project_id, :service, 'service_availability', :service,
                    99.9, 28, 'yes', :key, :first, :first, :deleted)
                RETURNING id
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "service": service,
                "key": key,
                "first": LONG_AGO,
                "deleted": deleted,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO slo_target_changes (org_id, project_id, slo_id, what) "
                "VALUES (:org_id, :project_id, :id, 'deleted')"
            ),
            {"org_id": tenant.org_id, "project_id": tenant.project_id, "id": made},
        )
    return made


async def _days(
    engine: AsyncEngine, tenant: AlertTenant, target: uuid.UUID, days: list[date], *, good: int
) -> None:
    async with engine.begin() as connection:
        for day in days:
            await connection.execute(
                text(
                    """
                    INSERT INTO slo_daily_counts (org_id, slo_id, day, good, total, failures,
                        quiet_minutes, lost_minutes, quiet_leading_minutes,
                        quiet_trailing_minutes, longest_quiet_minutes, definition_version,
                        final, computed_at)
                    VALUES (:org_id, :id, :day, :good, 10000, :failures, 0, 0, 0, 0, 0,
                            1, true, now())
                    """
                ),
                {
                    "org_id": tenant.org_id,
                    "id": target,
                    "day": day,
                    "good": good,
                    "failures": 10_000 - good,
                },
            )


def _month_days(month: date) -> list[date]:
    return [month + timedelta(days=offset) for offset in range(slo.days_in_month(month))]


async def _rows(engine: AsyncEngine, statement: str, **keys: Any) -> list[Any]:
    async with engine.connect() as connection:
        return list(await connection.execute(text(statement), keys))


async def test_ac71_a_target_deleted_30_days_ago_is_purged_and_keeps_its_months(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    deleted_ago = timedelta(days=31)
    gone = await _target(engine, tenant, deleted_ago=deleted_ago, service="checkout")
    kept = await _target(engine, tenant, deleted_ago=timedelta(days=29), service="cart")
    deleted_on = (datetime.now(UTC) - deleted_ago).date()
    whole = slo.previous_month(slo.month_start(deleted_on))
    under_way = slo.month_start(deleted_on)
    await _days(engine, tenant, gone, _month_days(whole), good=9_980)
    await _days(engine, tenant, gone, [under_way], good=10_000)
    await _days(engine, tenant, kept, [whole], good=10_000)

    await expiry.sweep(engine)

    targets = await _rows(
        engine, "SELECT id FROM slo_targets WHERE org_id = :org_id", org_id=tenant.org_id
    )
    assert [row.id for row in targets] == [kept]
    days = await _rows(
        engine, "SELECT slo_id FROM slo_daily_counts WHERE org_id = :org_id", org_id=tenant.org_id
    )
    assert {row.slo_id for row in days} == {kept}
    months = await _rows(
        engine,
        "SELECT * FROM slo_deleted_months WHERE org_id = :org_id ORDER BY month",
        org_id=tenant.org_id,
    )
    # Only the month that ended before the deletion; the one under way was never complete.
    assert [(row.slo_id, row.month, row.result) for row in months] == [(gone, whole, "missed")]
    (month,) = months
    assert month.label == "checkout availability, 99.9% over 28 days"
    assert (month.figure_percent, month.objective_percent) == (Decimal("99.80"), Decimal("99.90"))
    assert month.days_held == month.days_in_month == slo.days_in_month(whole)
    assert month.upstream_sampling == "yes"
    assert month.project_id == tenant.project_id
    history = await _rows(
        engine,
        "SELECT slo_id FROM slo_target_changes WHERE org_id = :org_id",
        org_id=tenant.org_id,
    )
    assert {row.slo_id for row in history} == {gone, kept}


async def test_a_purge_run_twice_writes_each_month_once(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    gone = await _target(engine, tenant, deleted_ago=timedelta(days=45), service="checkout")
    deleted_on = (datetime.now(UTC) - timedelta(days=45)).date()
    await _days(
        engine, tenant, gone, _month_days(slo.previous_month(slo.month_start(deleted_on))), good=1
    )

    await expiry.sweep(engine)
    await expiry.sweep(engine)

    months = await _rows(
        engine, "SELECT month FROM slo_deleted_months WHERE org_id = :org_id", org_id=tenant.org_id
    )
    assert len(months) == 1


async def test_ac88_figures_changes_and_purged_months_lapse_at_400_days(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    target = await _target(engine, tenant, deleted_ago=None, service="checkout")
    today = datetime.now(UTC).date()
    old_day, recent_day = today - KEPT - DAY, today - KEPT + DAY
    await _days(engine, tenant, target, [old_day, recent_day], good=10_000)
    old_month = slo.month_start(today - KEPT - timedelta(days=40))
    recent_month = slo.month_start(today - timedelta(days=200))
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO slo_target_changes (org_id, project_id, slo_id, what, at) "
                "VALUES (:org_id, :project_id, :id, 'changed', now() - interval '401 days')"
            ),
            {"org_id": tenant.org_id, "project_id": tenant.project_id, "id": target},
        )
        for month in (old_month, recent_month):
            await connection.execute(
                text(
                    """
                    INSERT INTO slo_deleted_months (org_id, project_id, slo_id, month, kind,
                        label, objective_percent, result, figure_percent, good, total,
                        coverage, quiet_minutes, longest_quiet_minutes, lost_minutes,
                        excluded_minutes, days_held, days_in_month, loss_known, deleted_at)
                    VALUES (:org_id, :project_id, :id, :month, 'service_availability',
                        'cart availability, 99.9% over 28 days', 99.9, 'met', 100, 10, 10,
                        1, 0, 0, 0, 0, 28, :days, true, now())
                    """
                ),
                {
                    "org_id": tenant.org_id,
                    "project_id": tenant.project_id,
                    "id": uuid.uuid4(),
                    "month": month,
                    "days": slo.days_in_month(month),
                },
            )

    await expiry.sweep(engine)

    days = await _rows(
        engine, "SELECT day FROM slo_daily_counts WHERE org_id = :org_id", org_id=tenant.org_id
    )
    assert [row.day for row in days] == [recent_day]
    changes = await _rows(
        engine, "SELECT what FROM slo_target_changes WHERE org_id = :org_id", org_id=tenant.org_id
    )
    assert [row.what for row in changes] == ["deleted"]
    months = await _rows(
        engine, "SELECT month FROM slo_deleted_months WHERE org_id = :org_id", org_id=tenant.org_id
    )
    assert [row.month for row in months] == [recent_month]

"""The service conditions against real Postgres, with the telemetry store replaced
by readings the test chooses (F21: AC8, AC9, AC10, FR6).

What is replaced is exactly the ClickHouse read; everything it decides — positions,
incidents, the error groups seen, the watermark — is written for real. The two
statements themselves run against real ClickHouse in
`test_alert_telemetry_live.py`.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

import pytest
from alert_fixtures import AlertTenant, add_rule, future, postgres_reachable, rows
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import new_errors, silence, telemetry, thresholds
from nexovue_scheduler.alerts.telemetry import TelemetryStore

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_alert_service_conditions_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


class _Readings:
    """What the telemetry store would say, minute by minute — for one tenant.

    The sweeps read every tenant's rules, and this database is shared with the running
    stack, so any other tenant asked is answered with nothing."""

    def __init__(self, tenant: AlertTenant) -> None:
        self._tenant = tenant
        self.services: list[dict[str, Any]] = []
        self.groups: list[dict[str, Any]] = []
        self.asked: list[tuple[str, str]] = []

    def for_project(self, org_id: object, project_id: object) -> _Readings:
        self.asked.append((str(org_id), str(project_id)))
        if org_id == self._tenant.org_id:
            return self
        return _Readings(self._tenant)

    def service_readings(self, *, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self.services

    def error_groups(
        self, *, received_from: datetime, received_to: datetime
    ) -> list[dict[str, Any]]:
        # As the statement reads: the window, oldest first received, at most the limit.
        inside = [
            group for group in self.groups if received_from <= group["first_received"] < received_to
        ]
        inside.sort(key=lambda group: (group["first_received"], group["group_id"]))
        return inside[: telemetry.MOST_GROUPS]


def _store(readings: _Readings) -> TelemetryStore:
    return cast("TelemetryStore", readings)


async def _rule(engine: AsyncEngine, tenant: AlertTenant, condition: dict[str, Any]) -> None:
    async with engine.begin() as connection:
        rule_id = await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name=f"{condition['kind']} rule",
            condition=condition,
            channel_ids=[tenant.owners_channel_id],
        )
        await connection.execute(
            text("UPDATE alert_rules SET condition_kind = :kind WHERE id = :id"),
            {"kind": condition["kind"], "id": rule_id},
        )
        # The default check rule stays out of these suites' way.
        await connection.execute(
            text("UPDATE alert_rules SET enabled = false WHERE id = :id"),
            {"id": tenant.default_rule_id},
        )


async def _incidents(engine: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    return await rows(
        engine,
        "SELECT subject_label, resolved_at, is_event, detail FROM incidents "
        "WHERE org_id = :org_id ORDER BY opened_at",
        org_id=tenant.org_id,
    )


async def test_an_error_rate_fires_after_its_wait_and_resolves_after_its_own(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC9, through the evaluator: positions persist between minutes."""
    await _rule(
        engine,
        tenant,
        {
            "kind": "service_error_rate",
            "threshold_percent": 5,
            "min_requests": 20,
            "fire_after_minutes": 5,
            "resolve_after_minutes": 5,
        },
    )
    readings = _Readings(tenant)
    start = future()
    breached = {"service_name": "checkout", "request_count": 200, "error_count": 30, "p95_ms": 90.0}
    healthy = breached | {"error_count": 0}

    for minute in range(7):
        readings.services = [breached]
        await thresholds.evaluate(
            engine, _store(readings), now=start + timedelta(minutes=minute), org_id=tenant.org_id
        )
    [opened] = await _incidents(engine, tenant)
    assert opened.subject_label == "checkout"
    assert opened.detail["value"] == 15.0
    assert opened.detail["threshold"] == 5
    assert opened.resolved_at is None

    for minute in range(7, 13):
        readings.services = [healthy]
        await thresholds.evaluate(
            engine, _store(readings), now=start + timedelta(minutes=minute), org_id=tenant.org_id
        )
    [closed] = await _incidents(engine, tenant)
    assert closed.resolved_at is not None
    # This tenant's rule was read through this tenant's project and no other.
    assert {project for org, project in readings.asked if org == str(tenant.org_id)} == {
        str(tenant.project_id)
    }


async def test_a_subject_the_database_refuses_does_not_stop_the_others(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """A service name is whatever a customer's SDK sent; one Postgres cannot store (a
    NUL) must cost only its own judgment, not every service and tenant after it."""
    await _rule(
        engine,
        tenant,
        {"kind": "service_error_rate", "threshold_percent": 5, "fire_after_minutes": 1},
    )
    readings = _Readings(tenant)
    breached = {"service_name": "checkout", "request_count": 200, "error_count": 30, "p95_ms": 9.0}
    readings.services = [breached | {"service_name": "bad\x00name"}, breached]
    start = future()
    for minute in range(3):
        await thresholds.evaluate(
            engine, _store(readings), now=start + timedelta(minutes=minute), org_id=tenant.org_id
        )
    [opened] = await _incidents(engine, tenant)
    assert opened.subject_label == "checkout"


async def test_a_wait_counts_in_whole_minutes_whatever_second_the_sweep_runs(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """Breached from the 12:00 run (at :03) to the 12:05 run (at :01) is five minutes
    of breach: a five-minute wait fires then, not a minute later."""
    await _rule(
        engine,
        tenant,
        {"kind": "service_error_rate", "threshold_percent": 5, "fire_after_minutes": 5},
    )
    readings = _Readings(tenant)
    readings.services = [
        {"service_name": "checkout", "request_count": 200, "error_count": 30, "p95_ms": 9.0}
    ]
    start = future().replace(second=3, microsecond=0)
    for at in (start, start + timedelta(minutes=4, seconds=58)):
        await thresholds.evaluate(engine, _store(readings), now=at, org_id=tenant.org_id)
    [opened] = await _incidents(engine, tenant)
    assert opened.subject_label == "checkout"


async def test_below_the_minimum_request_count_nothing_fires(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR6."""
    await _rule(
        engine,
        tenant,
        {
            "kind": "service_error_rate",
            "threshold_percent": 5,
            "min_requests": 20,
            "fire_after_minutes": 0,
        },
    )
    readings = _Readings(tenant)
    readings.services = [
        {"service_name": "checkout", "request_count": 8, "error_count": 5, "p95_ms": 1.0}
    ]
    start = future()
    for minute in range(10):
        await thresholds.evaluate(
            engine, _store(readings), now=start + timedelta(minutes=minute), org_id=tenant.org_id
        )
    assert await _incidents(engine, tenant) == []
    [position] = await rows(
        engine,
        "SELECT phase FROM alert_evaluations WHERE org_id = :org_id",
        org_id=tenant.org_id,
    )
    assert position.phase == "insufficient"


async def test_a_service_that_stops_sending_goes_silent_and_comes_back(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC10."""
    await _rule(engine, tenant, {"kind": "service_silent", "silent_minutes": 10})
    now = future()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO service_last_seen (org_id, project_id, service_name, last_seen_at) "
                "VALUES (:org, :project, 'cart', :quiet), (:org, :project, 'checkout', :live)"
            ),
            {
                "org": tenant.org_id,
                "project": tenant.project_id,
                "quiet": now - timedelta(minutes=12),
                "live": now - timedelta(seconds=30),
            },
        )
    await silence.evaluate(engine, now=now, org_id=tenant.org_id)
    [opened] = await _incidents(engine, tenant)
    assert opened.subject_label == "cart"

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE service_last_seen SET last_seen_at = :now WHERE service_name = 'cart' "
                "AND project_id = :project"
            ),
            {"now": now + timedelta(minutes=1), "project": tenant.project_id},
        )
    await silence.evaluate(engine, now=now + timedelta(minutes=1, seconds=10), org_id=tenant.org_id)
    [closed] = await _incidents(engine, tenant)
    assert closed.resolved_at is not None


async def test_a_new_error_is_said_once_and_the_history_before_the_rule_never(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC8, and the baseline."""
    await _rule(engine, tenant, {"kind": "new_error_group"})
    readings = _Readings(tenant)
    start = future()
    old = {
        "group_id": "aaa",
        "exception_type": "TimeoutError",
        "services": ["checkout"],
        "first_seen": start - timedelta(days=3),
        "first_received": start - timedelta(days=3),
    }
    readings.groups = [old]
    await new_errors.evaluate(engine, _store(readings), now=start, org_id=tenant.org_id)
    assert await _incidents(engine, tenant) == []

    fresh = {
        "group_id": "bbb",
        "exception_type": "KeyError",
        "services": ["cart"],
        "first_seen": start,
        "first_received": start,
    }
    readings.groups = [old, fresh]
    await new_errors.evaluate(
        engine, _store(readings), now=start + timedelta(minutes=1), org_id=tenant.org_id
    )
    await new_errors.evaluate(
        engine, _store(readings), now=start + timedelta(minutes=2), org_id=tenant.org_id
    )

    [event] = await _incidents(engine, tenant)
    assert event.subject_label == "KeyError in cart"
    assert event.is_event is True
    assert event.resolved_at is not None
    kinds = await rows(
        engine, "SELECT kind FROM notifications WHERE org_id = :org_id", org_id=tenant.org_id
    )
    assert [row.kind for row in kinds] == ["opened"]
    [watermark] = await rows(
        engine,
        "SELECT received_through FROM error_group_watermarks WHERE project_id = :project",
        project=tenant.project_id,
    )
    assert watermark.received_through == start + timedelta(minutes=2) - new_errors.SETTLING


async def test_a_history_larger_than_one_read_is_baselined_in_parts_and_never_announced(
    engine: AsyncEngine, tenant: AlertTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read stops at the group limit. The rest of the history must still be marked
    seen before anything is announced, or each of it would later be called new."""
    monkeypatch.setattr(telemetry, "MOST_GROUPS", 2)
    await _rule(engine, tenant, {"kind": "new_error_group"})
    readings = _Readings(tenant)
    start = future()
    readings.groups = [
        {
            "group_id": f"old-{day}",
            "exception_type": "TimeoutError",
            "services": ["checkout"],
            "first_seen": start - timedelta(days=day),
            "first_received": start - timedelta(days=day),
        }
        for day in (9, 7, 5, 3, 1)
    ]
    for minute in range(6):
        await new_errors.evaluate(
            engine, _store(readings), now=start + timedelta(minutes=minute), org_id=tenant.org_id
        )
    assert await _incidents(engine, tenant) == []
    seen = await rows(
        engine,
        "SELECT count(*) AS n FROM error_groups_seen WHERE project_id = :project",
        project=tenant.project_id,
    )
    assert seen[0].n == 5

    readings.groups.append(
        {
            "group_id": "fresh",
            "exception_type": "KeyError",
            "services": ["cart"],
            "first_seen": start + timedelta(minutes=6),
            "first_received": start + timedelta(minutes=6),
        }
    )
    for minute in (8, 9):
        await new_errors.evaluate(
            engine, _store(readings), now=start + timedelta(minutes=minute), org_id=tenant.org_id
        )
    [event] = await _incidents(engine, tenant)
    assert event.subject_label == "KeyError in cart"

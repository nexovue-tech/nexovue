"""A check's state change opens, escalates and resolves incidents, through the real
entry point — `state_store.record` — against real Postgres (AC5, AC7, AC16, AC27).

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from alert_fixtures import AlertTenant, add_channel, add_rule, future, postgres_reachable, rows
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.probes import state_store

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_alert_check_state_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

START = future()


async def _record(
    engine: AsyncEngine, tenant: AlertTenant, outcome: str, minute: int
) -> state_store.Recorded:
    return await state_store.record(
        engine,
        state_store.RecordedOutcome(
            check_id=str(tenant.check_id),
            org_id=str(tenant.org_id),
            project_id=str(tenant.project_id),
            outcome=outcome,
            run_id=f"run-{minute}",
            location="local",
            observed_at=START + timedelta(minutes=minute),
            reason="expected status code less than 400, got 503, body {secret}",
            alert_reason="expected status code less than 400, got 503",
        ),
    )


async def _incidents(engine: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    return await rows(
        engine,
        "SELECT * FROM incidents WHERE org_id = :org_id ORDER BY opened_at",
        org_id=tenant.org_id,
    )


async def _notifications(engine: AsyncEngine, tenant: AlertTenant) -> list[tuple[str, str, str]]:
    found = await rows(
        engine,
        """
        SELECT notification_channels.name, notifications.kind, notifications.severity
        FROM notifications
        JOIN notification_channels ON notification_channels.id = notifications.channel_id
        WHERE notifications.org_id = :org_id
        ORDER BY notifications.created_at, notification_channels.name
        """,
        org_id=tenant.org_id,
    )
    return [(row.name, row.kind, row.severity) for row in found]


async def test_a_check_going_down_opens_one_incident_and_tells_the_owners(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    recorded = await _record(engine, tenant, "down", 0)

    assert recorded == state_store.Recorded(changed_to="down", notifications=1)
    [incident] = await _incidents(engine, tenant)
    assert incident.subject_label == "Checkout API"
    assert incident.severity == "critical"
    assert incident.resolved_at is None
    # The alert's reason, not the run's: the run's quotes the body (BR9).
    assert incident.detail["reason"] == "expected status code less than 400, got 503"
    assert await _notifications(engine, tenant) == [("Organization owners", "opened", "critical")]


async def test_recovery_resolves_it_and_tells_the_same_channels(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    await _record(engine, tenant, "down", 0)
    recorded = await _record(engine, tenant, "up", 7)

    assert recorded.notifications == 1
    [incident] = await _incidents(engine, tenant)
    assert incident.resolved_at == START + timedelta(minutes=7)
    assert [kind for _, kind, _ in await _notifications(engine, tenant)] == ["opened", "resolved"]


async def test_staying_down_is_still_one_incident(engine: AsyncEngine, tenant: AlertTenant) -> None:
    """BR1: a second confirmed *down* is not a state change and opens nothing."""
    await _record(engine, tenant, "down", 0)
    again = await _record(engine, tenant, "down", 1)

    assert again == state_store.Recorded()
    assert len(await _incidents(engine, tenant)) == 1


async def test_misconfigured_does_not_notify_a_rule_that_asked_only_for_down(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC7."""
    recorded = await _record(engine, tenant, "misconfigured", 0)

    assert recorded == state_store.Recorded(changed_to="misconfigured", notifications=0)
    assert await _incidents(engine, tenant) == []


async def test_degraded_then_down_is_one_incident_escalated_once(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC27."""
    async with engine.begin() as connection:
        oncall = await add_channel(
            connection,
            tenant.org_id,
            kind="email",
            name="On call",
            config='{"addresses": ["oncall@example.test"]}',
        )
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Checkout health",
            condition={
                "kind": "check_state",
                "states": {"degraded": "warning", "down": "critical"},
            },
            channel_ids=[oncall],
        )

    await _record(engine, tenant, "degraded", 0)
    await _record(engine, tenant, "down", 1)

    on_call = [row for row in await _notifications(engine, tenant) if row[0] == "On call"]
    assert on_call == [("On call", "opened", "warning"), ("On call", "escalated", "critical")]
    checkout_health = [
        incident
        for incident in await _incidents(engine, tenant)
        if incident.rule_name == "Checkout health"
    ]
    assert [(incident.severity, incident.resolved_at) for incident in checkout_health] == [
        ("critical", None)
    ]


async def test_a_muted_rule_records_the_incident_and_sends_nothing_about_it(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC16, BR5 — including its recovery, which fired during the mute too."""
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE alert_rules SET muted_until = :until WHERE id = :id"),
            {"until": START + timedelta(hours=1), "id": tenant.default_rule_id},
        )

    down = await _record(engine, tenant, "down", 0)
    up = await _record(engine, tenant, "up", 5)

    assert (down.notifications, up.notifications) == (0, 0)
    [incident] = await _incidents(engine, tenant)
    assert incident.muted is True
    assert incident.resolved_at is not None
    assert await _notifications(engine, tenant) == []


async def test_a_recovery_during_a_mute_still_closes_what_a_machine_was_sent(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """Decision 2026-09-19: people hear nothing during a mute (BR5), but a webhook or
    PagerDuty told of the opening before the mute would otherwise keep it open."""
    async with engine.begin() as connection:
        hook = await add_channel(connection, tenant.org_id, kind="webhook", name="Ops bot")
        await connection.execute(
            text(
                "INSERT INTO alert_rule_channels (org_id, rule_id, channel_id) VALUES (:o, :r, :c)"
            ),
            {"o": tenant.org_id, "r": tenant.default_rule_id, "c": hook},
        )
    await _record(engine, tenant, "down", 0)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE alert_rules SET muted_until = :until WHERE id = :id"),
            {"until": START + timedelta(hours=1), "id": tenant.default_rule_id},
        )
    await _record(engine, tenant, "up", 5)

    said = sorted((name, kind) for name, kind, _ in await _notifications(engine, tenant))
    assert said == [
        ("Ops bot", "opened"),
        ("Ops bot", "resolved"),
        ("Organization owners", "opened"),
    ]


async def test_a_rule_scoped_to_other_checks_ignores_this_one(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            "UPDATE alert_rules SET condition = jsonb_set(condition, '{check_ids}', "
            f"'[\"{uuid.uuid4()}\"]') WHERE id = '{tenant.default_rule_id}'"
        )

    recorded = await _record(engine, tenant, "down", 0)

    assert recorded.notifications == 0
    assert await _incidents(engine, tenant) == []


async def test_a_failure_writing_the_alert_undoes_the_state_change_too(
    engine: AsyncEngine, tenant: AlertTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state and its alerts commit together or not at all (design, *Check
    state — inline*): a state recorded without its alert would be an outage told
    to nobody, and `record_outcome`'s retry would then see no change to alert on."""

    async def broken(*_: object, **__: object) -> int:
        raise RuntimeError("the alert write failed")

    monkeypatch.setattr(state_store, "on_check_state_change", broken)
    with pytest.raises(RuntimeError, match="the alert write failed"):
        await _record(engine, tenant, "down", 0)

    assert (
        await rows(engine, "SELECT 1 FROM check_states WHERE check_id = :id", id=tenant.check_id)
        == []
    )
    assert (
        await rows(
            engine, "SELECT 1 FROM check_state_changes WHERE check_id = :id", id=tenant.check_id
        )
        == []
    )

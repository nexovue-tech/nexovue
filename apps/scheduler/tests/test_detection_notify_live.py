"""Who is told what about a finding, against real Postgres (plan T4.2; spec FR28,
FR29, BR9; AC19, AC22, AC31, AC44, AC45; design *Notifications*).

Findings are written as the project job writes them, and `notify` is called as
the job calls it, so each rule of what is sent is judged on its own. The project
job's own wiring is judged in `test_detection_project_job_live.py`.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from alert_fixtures import (
    AlertTenant,
    add_channel,
    add_finding,
    add_rule,
    anomaly_rule,
    future,
    postgres_reachable,
    rows,
)
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts.incidents import FLAP_OPENINGS, FLAP_QUIET
from nexovue_scheduler.detection import notify

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_detection_notify_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

MINUTE = timedelta(minutes=1)


async def _channel(engine: AsyncEngine, tenant: AlertTenant, kind: str) -> uuid.UUID:
    async with engine.begin() as connection:
        return await add_channel(connection, tenant.org_id, kind=kind, name=kind.title())


async def _rule(
    engine: AsyncEngine,
    tenant: AlertTenant,
    channels: list[uuid.UUID],
    *,
    minimum_severity: str = "warning",
    services: list[str] | None = None,
) -> uuid.UUID:
    async with engine.begin() as connection:
        return await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Findings",
            condition=anomaly_rule(minimum_severity, services),
            channel_ids=channels,
        )


async def _finding(
    engine: AsyncEngine, tenant: AlertTenant, at: datetime, **fields: Any
) -> uuid.UUID:
    async with engine.begin() as connection:
        return await add_finding(connection, tenant, started_at=at - 5 * MINUTE, **fields)


async def _close(
    engine: AsyncEngine, tenant: AlertTenant, finding_id: uuid.UUID, at: datetime
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE findings SET state = 'closed', closed_reason = 'recovered', "
                "closed_at = :at WHERE org_id = :org_id AND id = :id"
            ),
            {"org_id": tenant.org_id, "id": finding_id, "at": at},
        )


async def _call(engine: AsyncEngine, action: Any, *args: Any, **kwargs: Any) -> int:
    async with engine.begin() as connection:
        written: int = await action(connection, *args, **kwargs)
        return written


async def _told(engine: AsyncEngine, tenant: AlertTenant) -> list[tuple[str, uuid.UUID]]:
    found = await rows(
        engine,
        "SELECT kind, channel_id FROM notifications WHERE org_id = :org_id "
        "ORDER BY created_at, kind, channel_id",
        org_id=tenant.org_id,
    )
    return [(row.kind, row.channel_id) for row in found]


async def _service_settings(
    engine: AsyncEngine,
    tenant: AlertTenant,
    *,
    muted_until: datetime | None = None,
    snoozed_until: datetime | None = None,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO service_detection_settings
                    (org_id, project_id, service_name, muted_until, snoozed_until)
                VALUES (:org_id, :project_id, 'checkout', :muted_until, :snoozed_until)
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "muted_until": muted_until,
                "snoozed_until": snoozed_until,
            },
        )


# ------------------------------------------------------------- one finding


async def test_a_finding_s_life_on_slack_is_one_message_per_event_and_no_more(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC19, AC45: opened, a signal joining, critical and closed are one message each.
    Being told twice about one event — a retried job — writes nothing more (NFR3)."""
    slack = await _channel(engine, tenant, "slack")
    await _rule(engine, tenant, [slack])
    at = future()
    finding = await _finding(engine, tenant, at)
    keys = {"org_id": tenant.org_id}

    assert (
        await _call(engine, notify.finding_opened, finding, severity="warning", at=at, **keys) == 1
    )
    assert (
        await _call(engine, notify.finding_opened, finding, severity="warning", at=at, **keys) == 0
    )
    joined = {"severity": "warning", "keys": 2, "at": at + MINUTE}
    assert await _call(engine, notify.finding_joined, finding, **joined, **keys) == 1
    assert await _call(engine, notify.finding_joined, finding, **joined, **keys) == 0
    assert await _call(engine, notify.finding_escalated, finding, at=at + 2 * MINUTE, **keys) == 1
    assert await _call(engine, notify.finding_escalated, finding, at=at + 3 * MINUTE, **keys) == 0
    await _close(engine, tenant, finding, at + 30 * MINUTE)
    closed = {"severity": "critical", "at": at + 30 * MINUTE}
    assert await _call(engine, notify.finding_closed, finding, **closed, **keys) == 1

    assert await _told(engine, tenant) == [
        ("opened", slack),
        ("joined", slack),
        ("escalated", slack),
        ("resolved", slack),
    ]


async def test_each_new_signal_joining_is_its_own_message(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """D29: a second signal, and then an endpoint, each send one."""
    slack = await _channel(engine, tenant, "slack")
    await _rule(engine, tenant, [slack])
    at = future()
    finding = await _finding(engine, tenant, at)
    await _call(
        engine, notify.finding_opened, finding, org_id=tenant.org_id, severity="warning", at=at
    )
    for keys in (2, 3):
        written = await _call(
            engine,
            notify.finding_joined,
            finding,
            org_id=tenant.org_id,
            severity="warning",
            keys=keys,
            at=at + keys * MINUTE,
        )
        assert written == 1
    assert [kind for kind, _ in await _told(engine, tenant)] == ["opened", "joined", "joined"]


async def test_the_default_rule_sends_a_warning_nothing_until_it_turns_critical(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC22, FR29: *Critical problems* hears a warning only once it is critical, and
    then its close, because it was told of the escalation."""
    await _rule(engine, tenant, [tenant.owners_channel_id], minimum_severity="critical")
    at = future()
    finding = await _finding(engine, tenant, at)
    keys = {"org_id": tenant.org_id}

    assert (
        await _call(engine, notify.finding_opened, finding, severity="warning", at=at, **keys) == 0
    )
    assert await _call(engine, notify.finding_escalated, finding, at=at + MINUTE, **keys) == 1
    await _close(engine, tenant, finding, at + 30 * MINUTE)
    closed = {"severity": "critical", "at": at + 30 * MINUTE}
    assert await _call(engine, notify.finding_closed, finding, **closed, **keys) == 1
    assert await _told(engine, tenant) == [
        ("escalated", tenant.owners_channel_id),
        ("resolved", tenant.owners_channel_id),
    ]


async def test_a_critical_finding_emails_the_owners_opened_then_resolved(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC22: one *opened* and one *resolved*, and nothing for a warning."""
    await _rule(engine, tenant, [tenant.owners_channel_id], minimum_severity="critical")
    at = future()
    critical = await _finding(engine, tenant, at, severity="critical")
    keys = {"org_id": tenant.org_id}
    assert (
        await _call(engine, notify.finding_opened, critical, severity="critical", at=at, **keys)
        == 1
    )
    await _close(engine, tenant, critical, at + 30 * MINUTE)
    closed = {"severity": "critical", "at": at + 30 * MINUTE}
    assert await _call(engine, notify.finding_closed, critical, **closed, **keys) == 1
    assert [kind for kind, _ in await _told(engine, tenant)] == ["opened", "resolved"]


async def test_a_closing_goes_to_the_channels_told_whatever_the_rules_became(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """Design, *What a finding sends*: the alert PagerDuty triggered is the one resolved,
    though the rule that sent it was deleted meanwhile."""
    pagerduty = await _channel(engine, tenant, "pagerduty")
    rule = await _rule(engine, tenant, [pagerduty])
    at = future()
    finding = await _finding(engine, tenant, at)
    keys = {"org_id": tenant.org_id}
    await _call(engine, notify.finding_opened, finding, severity="warning", at=at, **keys)
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM alert_rules WHERE id = :id"), {"id": rule})
    await _close(engine, tenant, finding, at + 30 * MINUTE)

    closed = {"severity": "warning", "at": at + 30 * MINUTE}
    assert await _call(engine, notify.finding_closed, finding, **closed, **keys) == 1
    assert (await _told(engine, tenant))[-1] == ("resolved", pagerduty)


async def test_a_rule_routes_only_the_findings_it_selects(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR28: its services and minimum severity; a muted rule sends nothing (F21 BR5).
    A project finding (FR15) matches a rule naming any service it holds."""
    listed, muted = await _channel(engine, tenant, "slack"), await _channel(engine, tenant, "teams")
    await _rule(engine, tenant, [listed], services=["cart"])
    quiet_rule = await _rule(engine, tenant, [muted])
    at = future()
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE alert_rules SET muted_until = :until WHERE id = :id"),
            {"until": at + timedelta(hours=1), "id": quiet_rule},
        )
    checkout = await _finding(engine, tenant, at)
    keys = {"org_id": tenant.org_id}
    assert (
        await _call(engine, notify.finding_opened, checkout, severity="warning", at=at, **keys) == 0
    )

    project = await _finding(engine, tenant, at, service=None, severity="critical")
    opened = await _call(
        engine,
        notify.finding_opened,
        project,
        severity="critical",
        services=["cart", "checkout"],
        at=at,
        **keys,
    )
    assert opened == 1
    assert await _told(engine, tenant) == [("opened", listed)]


# ------------------------------------------------------------ mute and snooze


async def test_a_finding_opened_muted_sends_nothing_ever(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC31, BR9: not when it opens, escalates, gains a signal or closes."""
    slack = await _channel(engine, tenant, "slack")
    await _rule(engine, tenant, [slack])
    at = future()
    finding = await _finding(engine, tenant, at, muted=True)
    keys = {"org_id": tenant.org_id}

    assert (
        await _call(engine, notify.finding_opened, finding, severity="warning", at=at, **keys) == 0
    )
    assert await _call(engine, notify.finding_escalated, finding, at=at, **keys) == 0
    assert (
        await _call(
            engine, notify.finding_joined, finding, severity="critical", keys=2, at=at, **keys
        )
        == 0
    )
    await _close(engine, tenant, finding, at + 30 * MINUTE)
    closed = {"severity": "critical", "at": at + 30 * MINUTE}
    assert await _call(engine, notify.finding_closed, finding, **closed, **keys) == 0
    assert await _told(engine, tenant) == []


async def test_muting_a_service_mid_finding_stops_people_but_closes_the_machines_alert(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """Design, *Mute*: after a mute, only the recovery a PagerDuty or webhook channel
    needs to close what it was sent."""
    slack = await _channel(engine, tenant, "slack")
    pagerduty = await _channel(engine, tenant, "pagerduty")
    await _rule(engine, tenant, [slack, pagerduty])
    at = future()
    finding = await _finding(engine, tenant, at)
    keys = {"org_id": tenant.org_id}
    assert (
        await _call(engine, notify.finding_opened, finding, severity="warning", at=at, **keys) == 2
    )
    await _service_settings(engine, tenant, muted_until=at + timedelta(days=1))

    assert await _call(engine, notify.finding_escalated, finding, at=at + MINUTE, **keys) == 0
    await _close(engine, tenant, finding, at + 30 * MINUTE)
    closed = {"severity": "warning", "at": at + 30 * MINUTE}
    assert await _call(engine, notify.finding_closed, finding, **closed, **keys) == 1
    assert sorted(await _told(engine, tenant)) == sorted(
        [("opened", slack), ("opened", pagerduty), ("resolved", pagerduty)]
    )


async def test_a_snoozed_service_s_finding_tells_nobody(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """D25, FR22: a snooze sends nothing."""
    slack = await _channel(engine, tenant, "slack")
    await _rule(engine, tenant, [slack])
    at = future()
    await _service_settings(engine, tenant, snoozed_until=at + timedelta(hours=1))
    finding = await _finding(engine, tenant, at)
    assert (
        await _call(
            engine, notify.finding_opened, finding, org_id=tenant.org_id, severity="warning", at=at
        )
        == 0
    )
    assert await _told(engine, tenant) == []


# --------------------------------------------------------------- flapping


async def _earlier_openings(
    engine: AsyncEngine, tenant: AlertTenant, at: datetime, count: int
) -> None:
    """`count` findings on checkout in the last hour, each closed."""
    for step in range(count):
        started = at - timedelta(minutes=50) + step * 10 * MINUTE
        async with engine.begin() as connection:
            await add_finding(
                connection, tenant, started_at=started, closed_at=started + 5 * MINUTE
            )


async def test_a_fifth_opening_within_the_hour_is_said_once_as_flapping(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """C7, AC19: F21's guard, per project and service. The flapping finding sends no
    escalation; its close reaches only the machines that hold an alert for it."""
    slack = await _channel(engine, tenant, "slack")
    pagerduty = await _channel(engine, tenant, "pagerduty")
    await _rule(engine, tenant, [slack, pagerduty])
    at = future()
    await _earlier_openings(engine, tenant, at, FLAP_OPENINGS)
    fifth = await _finding(engine, tenant, at)
    keys = {"org_id": tenant.org_id}

    assert await _call(engine, notify.finding_opened, fifth, severity="warning", at=at, **keys) == 2
    assert {kind for kind, _ in await _told(engine, tenant)} == {"flapping"}
    [flagged] = await rows(engine, "SELECT flapping FROM findings WHERE id = :id", id=fifth)
    assert flagged.flapping is True
    assert await _call(engine, notify.finding_escalated, fifth, at=at + MINUTE, **keys) == 0
    await _close(engine, tenant, fifth, at + 20 * MINUTE)
    closed = {"severity": "warning", "at": at + 20 * MINUTE}
    assert await _call(engine, notify.finding_closed, fifth, **closed, **keys) == 1
    assert (await _told(engine, tenant))[-1] == ("resolved", pagerduty)


async def test_a_flapping_service_is_silent_until_it_has_been_quiet_for_30_minutes(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR11 as F21 has it: the next opening inside the quiet time sends nothing; one
    after it is an ordinary opening again, counted afresh."""
    slack = await _channel(engine, tenant, "slack")
    await _rule(engine, tenant, [slack])
    at = future()
    keys = {"org_id": tenant.org_id}
    async with engine.begin() as connection:
        await add_finding(
            connection,
            tenant,
            started_at=at - 20 * MINUTE,
            closed_at=at - 10 * MINUTE,
            flapping=True,
        )
    sixth = await _finding(engine, tenant, at)
    assert await _call(engine, notify.finding_opened, sixth, severity="warning", at=at, **keys) == 0
    later = at + FLAP_QUIET + 20 * MINUTE
    await _close(engine, tenant, sixth, at + 5 * MINUTE)
    calm = await _finding(engine, tenant, later)
    assert (
        await _call(engine, notify.finding_opened, calm, severity="warning", at=later, **keys) == 1
    )
    assert await _told(engine, tenant) == [("opened", slack)]


# ----------------------------------------------------------------- adoption


async def test_adoption_emails_the_owners_with_no_rule_at_all(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC44, D22: outside any rule, to the owners."""
    at = future()
    finding = await _finding(engine, tenant, at)
    written = await _call(engine, notify.finding_adopted, finding, org_id=tenant.org_id, at=at)
    assert written == 1
    [row] = await rows(
        engine,
        "SELECT kind, severity, channel_id, finding_id FROM notifications WHERE org_id = :org_id",
        org_id=tenant.org_id,
    )
    assert (row.kind, row.severity, row.channel_id, row.finding_id) == (
        "adopted",
        "warning",
        tenant.owners_channel_id,
        finding,
    )


async def test_a_muted_finding_s_adoption_is_not_sent(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """BR9 holds over D22: nothing is sent for a muted finding."""
    at = future()
    finding = await _finding(engine, tenant, at, muted=True)
    assert await _call(engine, notify.finding_adopted, finding, org_id=tenant.org_id, at=at) == 0

"""Noise controls against real Postgres (F21: FR10, FR11, FR12, FR15; AC11, AC12,
AC14, AC15).

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timedelta

from alert_fixtures import AlertTenant, future, postgres_reachable, rows
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import incidents, outbox
from nexovue_scheduler.probes import state_store

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_alert_noise_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


async def _record(engine: AsyncEngine, tenant: AlertTenant, outcome: str, at: datetime) -> None:
    await state_store.record(
        engine,
        state_store.RecordedOutcome(
            check_id=str(tenant.check_id),
            org_id=str(tenant.org_id),
            project_id=str(tenant.project_id),
            outcome=outcome,
            run_id=f"run-{at.isoformat()}",
            location="local",
            observed_at=at,
            reason=None,
        ),
    )


async def _kinds(engine: AsyncEngine, tenant: AlertTenant) -> list[str]:
    found = await rows(
        engine,
        "SELECT kind FROM notifications WHERE org_id = :org_id ORDER BY created_at, kind",
        org_id=tenant.org_id,
    )
    return [row.kind for row in found]


async def test_a_check_flipping_is_said_once_and_then_left_alone_until_it_settles(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC12: four ordinary flips, the fifth is *flapping*, then silence until the
    subject has stayed up for 30 minutes."""
    start = future()
    # Down at minutes 0, 2, 4, 6, 8 and 10: six openings in twelve minutes.
    for minute in range(0, 12):
        await _record(
            engine, tenant, "down" if minute % 2 == 0 else "up", start + timedelta(minutes=minute)
        )

    kinds = await _kinds(engine, tenant)
    assert kinds.count("flapping") == 1
    assert kinds.count("opened") == 4
    assert kinds.count("resolved") == 4

    flapping = await rows(
        engine,
        "SELECT count(*) AS n FROM incidents WHERE org_id = :org_id AND flapping",
        org_id=tenant.org_id,
    )
    assert flapping[0].n == 2  # the fifth and the sixth openings; only the fifth was said

    # Up for 31 minutes, then down again: an ordinary incident, said as one.
    await _record(engine, tenant, "down", start + timedelta(minutes=11 + 31))
    assert (await _kinds(engine, tenant)).count("opened") == 5


async def test_a_long_breach_with_no_repeat_is_two_messages(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC11."""
    start = future()
    await _record(engine, tenant, "down", start)
    async with engine.begin() as connection:
        assert (
            await incidents.renotify_due(
                connection, now=start + timedelta(hours=1), org_id=tenant.org_id
            )
            == 0
        )
    await _record(engine, tenant, "up", start + timedelta(hours=2))

    assert await _kinds(engine, tenant) == ["opened", "resolved"]


async def test_a_repeat_comes_at_the_interval_and_stops_at_five(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR10."""
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE alert_rules SET renotify_minutes = 30 WHERE id = :id"),
            {"id": tenant.default_rule_id},
        )
    start = future()
    await _record(engine, tenant, "down", start)

    written = []
    for minutes in (29, 31, 32, 62, 93, 124, 155, 186, 217):
        async with engine.begin() as connection:
            written.append(
                await incidents.renotify_due(
                    connection, now=start + timedelta(minutes=minutes), org_id=tenant.org_id
                )
            )
    assert written == [0, 1, 0, 1, 1, 1, 1, 0, 0]
    sequences = await rows(
        engine,
        "SELECT sequence FROM notifications WHERE org_id = :org_id AND kind = 'repeated' "
        "ORDER BY sequence",
        org_id=tenant.org_id,
    )
    assert [row.sequence for row in sequences] == [1, 2, 3, 4, 5]


async def _quiet_now(engine: AsyncEngine, tenant: AlertTenant, now: datetime) -> datetime:
    """Quiet hours on the owners channel covering `now`, ending an hour later."""
    start = (now - timedelta(hours=1)).time().replace(second=0, microsecond=0)
    end = (now + timedelta(hours=1)).time().replace(second=0, microsecond=0)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE notification_channels SET quiet_hours = CAST(:hours AS jsonb) "
                "WHERE id = :id"
            ),
            {
                "hours": json.dumps(
                    {
                        "timezone": "UTC",
                        "days": list(range(7)),
                        "start": start.isoformat(timespec="minutes"),
                        "end": end.isoformat(timespec="minutes"),
                    }
                ),
                "id": tenant.owners_channel_id,
            },
        )
    return datetime.combine(
        (now + timedelta(hours=1)).date(), time(end.hour, end.minute), now.tzinfo
    )


async def _write(engine: AsyncEngine, tenant: AlertTenant, severity: str, at: datetime) -> None:
    """One incident and its opening, as the scheduler would write them."""
    async with engine.begin() as connection:
        incident_id = (
            await connection.execute(
                text(
                    """
                    INSERT INTO incidents (org_id, project_id, rule_id, rule_name, condition_kind,
                        subject_key, subject_label, severity, opened_at, detail)
                    VALUES (:org, :project, :rule, 'Any check down', 'check_state', :subject,
                            :label, :severity, :at, '{"state": "down"}')
                    RETURNING id
                    """
                ),
                {
                    "org": tenant.org_id,
                    "project": tenant.project_id,
                    "rule": tenant.default_rule_id,
                    "subject": f"check:{uuid.uuid4()}",
                    "label": f"Check {uuid.uuid4().hex[:4]}",
                    "severity": severity,
                    "at": at,
                },
            )
        ).scalar_one()
        await connection.execute(
            text(
                """
                INSERT INTO notifications (org_id, project_id, incident_id, channel_id, kind,
                    severity, deliver_after, created_at)
                VALUES (:org, :project, :incident, :channel, 'opened', :severity, :at, :at)
                """
            ),
            {
                "org": tenant.org_id,
                "project": tenant.project_id,
                "incident": incident_id,
                "channel": tenant.owners_channel_id,
                "severity": severity,
                "at": at,
            },
        )


async def _claim(engine: AsyncEngine, tenant: AlertTenant, now: datetime) -> list[outbox.Batch]:
    async with engine.begin() as connection:
        claimed = await outbox.claim(connection, now=now, limit=100, org_id=tenant.org_id)
    return [batch for batch in claimed.batches if batch.channel.org_id == tenant.org_id]


async def test_quiet_hours_hold_warnings_and_let_criticals_through(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC15: the critical now; the warnings, together, when the quiet ends."""
    now = future()
    ends = await _quiet_now(engine, tenant, now)
    await _write(engine, tenant, "warning", now)
    await _write(engine, tenant, "warning", now)
    await _write(engine, tenant, "critical", now)

    [now_batch] = await _claim(engine, tenant, now)
    assert [notice.severity for notice in now_batch.notices] == ["critical"]
    await _deliver(engine, now_batch, now)

    assert await _claim(engine, tenant, ends - timedelta(minutes=1)) == []
    [digest] = await _claim(engine, tenant, ends + timedelta(seconds=1))
    assert [notice.severity for notice in digest.notices] == ["warning", "warning"]


async def _deliver(engine: AsyncEngine, batch: outbox.Batch, now: datetime) -> None:
    async with engine.begin() as connection:
        await outbox.record(connection, batch, outbox.SendResult(outcome="delivered"), now=now)


async def test_past_ten_a_minute_the_rest_go_as_one_message(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC14: fifteen in a minute is ten messages and one summary of the other five."""
    start = future()
    messages = 0
    for second in range(15):
        at = start + timedelta(seconds=second * 3)
        await _write(engine, tenant, "critical", at)
        for batch in await _claim(engine, tenant, at):
            await _deliver(engine, batch, at)
            messages += 1
    assert messages == 10

    [summary] = await _claim(engine, tenant, start + timedelta(seconds=61))
    assert len(summary.notices) == 5

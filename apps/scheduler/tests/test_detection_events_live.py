"""What the project job writes to the timeline, against real Postgres (F22, plan
T1.10; FR16, FR18, AC23).

`detection/events.py` is called beside the notify calls, never inside them, so a
muted finding — whose `notify.finding_opened` returns before the flap guard —
still gets its events, and a flapping spell gets exactly one. The job's own
wiring is judged by running these writers as the job runs them.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from alert_fixtures import AlertTenant, add_finding, postgres_reachable, rows
from live import require_stack
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts.incidents import FLAP_OPENINGS
from nexovue_scheduler.detection import events

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_detection_events_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

MINUTE = timedelta(minutes=1)
NOW = datetime(2026, 11, 2, 14, 2, tzinfo=UTC)


async def _finding(
    engine: AsyncEngine, tenant: AlertTenant, at: datetime, **fields: Any
) -> uuid.UUID:
    async with engine.begin() as connection:
        return await add_finding(connection, tenant, started_at=at, **fields)


async def _opened(
    engine: AsyncEngine, tenant: AlertTenant, finding_id: uuid.UUID, at: datetime, **fields: Any
) -> None:
    async with engine.begin() as connection:
        await events.finding_opened(
            connection,
            org_id=tenant.org_id,
            project_id=tenant.project_id,
            finding_id=finding_id,
            scope="service",
            service_name=fields.pop("service_name", "checkout"),
            severity=fields.pop("severity", "warning"),
            root_finding_id=fields.pop("root_finding_id", None),
            at=at,
        )


async def _events(engine: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    return await rows(
        engine,
        "SELECT finding_id, kind, actor_kind, detail FROM finding_events "
        "WHERE org_id = :org_id ORDER BY at, id",
        org_id=tenant.org_id,
    )


async def test_a_normal_finding_writes_opened_raised_joined_and_closed(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    finding_id = await _finding(engine, tenant, NOW)
    await _opened(engine, tenant, finding_id, NOW)
    async with engine.begin() as connection:
        await events.severity_raised(
            connection, org_id=tenant.org_id, finding_id=finding_id, at=NOW + MINUTE
        )
        await events.signal_joined(
            connection,
            org_id=tenant.org_id,
            finding_id=finding_id,
            signal_count=2,
            at=NOW + 2 * MINUTE,
        )
        await events.finding_closed(
            connection,
            org_id=tenant.org_id,
            finding_id=finding_id,
            reason="recovered",
            at=NOW + 3 * MINUTE,
        )

    written = await _events(engine, tenant)
    assert [(row.kind, row.actor_kind) for row in written] == [
        ("finding_opened", "system"),
        ("severity_raised", "system"),
        ("signal_joined", "system"),
        ("finding_closed", "system"),
    ]
    assert written[-1].detail == {"closed_reason": "recovered"}


async def test_a_snoozed_or_muted_finding_still_gets_its_events(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """Snooze and mute silence channels, not the record: the events are written
    beside the notify calls on purpose, because `finding_opened` returns on muted
    before the flap guard (design, *Timeline events from the job*)."""
    finding_id = await _finding(engine, tenant, NOW, muted=True)
    await _opened(engine, tenant, finding_id, NOW)

    written = await _events(engine, tenant)
    assert [row.kind for row in written] == ["finding_opened"]


async def test_a_flapping_spell_is_one_event_not_one_per_opening(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC23 for muted openings: five or more in an hour write one
    `flapping_started`, the openings and closings are not listed one by one, and
    F6's `flapping` column is untouched."""
    from sqlalchemy import text

    for index in range(FLAP_OPENINGS + 2):
        at = NOW + index * 2 * MINUTE
        finding_id = await _finding(engine, tenant, at, muted=True)
        await _opened(engine, tenant, finding_id, at)
        async with engine.begin() as connection:
            # The job closes the row and writes the event in one transaction.
            await connection.execute(
                text(
                    "UPDATE findings SET state = 'closed', closed_reason = 'recovered', "
                    "closed_at = :at WHERE org_id = :org_id AND id = :id"
                ),
                {"org_id": tenant.org_id, "id": finding_id, "at": at + MINUTE},
            )
            await events.finding_closed(
                connection,
                org_id=tenant.org_id,
                finding_id=finding_id,
                reason="recovered",
                at=at + MINUTE,
            )

    written = await _events(engine, tenant)
    kinds = [row.kind for row in written]
    # The first spell-free openings each write their pair; the fifth opening in
    # the hour starts the spell with exactly one event, and later openings and
    # every closing inside the spell write nothing.
    assert kinds.count("flapping_started") == 1
    assert kinds.count("finding_opened") == FLAP_OPENINGS - 1
    assert kinds.count("finding_closed") == FLAP_OPENINGS - 1
    flagged = await rows(
        engine,
        "SELECT count(*) AS flapping FROM findings WHERE org_id = :org_id AND flapping",
        org_id=tenant.org_id,
    )
    assert flagged[0].flapping == 0


async def test_a_closing_writes_an_event_only_if_its_opening_did(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR18's closing half: a finding whose opening fell inside a spell closes
    silently too, so a spell can never end with an orphan close row."""
    finding_id = await _finding(engine, tenant, NOW, muted=True)
    async with engine.begin() as connection:
        await events.finding_closed(
            connection,
            org_id=tenant.org_id,
            finding_id=finding_id,
            reason="recovered",
            at=NOW + MINUTE,
        )
    assert await _events(engine, tenant) == []


def test_the_jobs_kinds_are_in_the_frozen_vocabulary() -> None:
    """T1.1's vocabulary hold, beside the lifecycle: every kind this module
    writes passes the migration's check, so an insert cannot abort detection's
    transaction (AC24)."""
    from nexovue_platform.detection import CASE_STATES, FINDING_EVENT_KINDS, FINDING_STATES

    assert set(events.JOB_EVENT_KINDS) <= set(FINDING_EVENT_KINDS)
    # The two state vocabularies never collide: a sentence states both at once.
    assert set(CASE_STATES).isdisjoint(FINDING_STATES)

"""What beat reports about the queues and about detection, against real Postgres
(readiness blocker 7; F6 plan T7.1).

A due job, an alert waiting to be delivered, an erasure owed past three days, a
project detection has stopped judging, the detection sweep's own record and
detection's events are written in one transaction, read there, and rolled back —
so the dev stack's own workers never see them, and the readings move by exactly
what was written.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler import metrics
from nexovue_scheduler.detection import coordinator, holds
from nexovue_scheduler.erasure import owed

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_scheduler_metrics_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

WAITED = timedelta(minutes=10)
# The database's clock and this one's are not the same clock.
CLOCKS_APART = timedelta(seconds=5)
# Detection is *not running* for a project past 3 minutes (design 2.1, item 2).
STOPPED_FOR = timedelta(minutes=10)
JUST_RAN = timedelta(minutes=1)
# Older than *detection stalled*'s 10 minutes.
STALLED_FOR = timedelta(minutes=12)

_RUN: Final = text(
    """
    INSERT INTO detection_project_runs (org_id, project_id, last_completed_at, expected_since)
    VALUES (
        :org, :project,
        now() - CAST(:completed_ago AS interval), now() - CAST(:expected_ago AS interval)
    )
    ON CONFLICT (org_id, project_id) DO UPDATE
    SET last_completed_at = EXCLUDED.last_completed_at, expected_since = EXCLUDED.expected_since
    """
)


async def test_a_due_job_and_a_waiting_alert_and_an_overdue_erasure_are_counted(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    queue = f"metrics-test-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            before = await metrics.read_queues(connection, now=now)
            await connection.execute(
                text(
                    "INSERT INTO jobs (task, queue, kwargs, run_after, max_attempts, "
                    "soft_limit_seconds, hard_limit_seconds) "
                    "VALUES ('test.task', :queue, '{}', :due, 1, 5, 10)"
                ),
                {"queue": queue, "due": now - WAITED},
            )
            incident = (
                await connection.execute(
                    text(
                        "INSERT INTO incidents (org_id, project_id, rule_id, rule_name, "
                        "condition_kind, subject_key, subject_label, severity, opened_at, detail) "
                        "VALUES (:org, :project, :rule, 'Any check down', 'check_state', "
                        ":subject, 'A check', 'critical', now(), '{\"state\": \"down\"}') "
                        "RETURNING id"
                    ),
                    {
                        "org": tenant.org_id,
                        "project": tenant.project_id,
                        "rule": tenant.default_rule_id,
                        "subject": f"check:{uuid.uuid4()}",
                    },
                )
            ).scalar_one()
            await connection.execute(
                text(
                    "INSERT INTO notifications (org_id, project_id, incident_id, channel_id, "
                    "kind, severity, deliver_after, created_at) "
                    "VALUES (:org, :project, :incident, :channel, 'opened', 'critical', "
                    ":due, :due)"
                ),
                {
                    "org": tenant.org_id,
                    "project": tenant.project_id,
                    "incident": incident,
                    "channel": tenant.owners_channel_id,
                    "due": now - WAITED,
                },
            )
            await connection.execute(
                text("UPDATE organizations SET deleted_at = :at WHERE id = :id"),
                {"at": now - owed.OVERDUE_AFTER - timedelta(hours=1), "id": tenant.org_id},
            )

            after = await metrics.read_queues(connection, now=now)
        finally:
            await transaction.rollback()

    assert after.jobs_ready[queue] == 1
    assert after.oldest_ready_seconds[queue] >= (WAITED - CLOCKS_APART).total_seconds()
    assert after.alerts_waiting == before.alerts_waiting + 1
    assert after.oldest_alert_waiting_seconds >= (WAITED - CLOCKS_APART).total_seconds()
    assert after.erasures_overdue == before.erasures_overdue + 1


async def test_a_project_detection_has_not_judged_for_three_minutes_is_counted_behind(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """C8: install-wide, so the count and the oldest lag name no project. *Behind*
    is the API's *not running*: expected, and no run for 3 minutes."""
    keys = {"org": tenant.org_id, "project": tenant.project_id}
    a_day = timedelta(days=1)
    now = datetime.now(UTC)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                text("DELETE FROM detection_project_runs WHERE project_id = :project"), keys
            )
            before = await metrics.read_queues(connection, now=now)
            await connection.execute(
                _RUN, keys | {"completed_ago": JUST_RAN, "expected_ago": a_day}
            )
            judged_on_time = await metrics.read_queues(connection, now=now)
            await connection.execute(
                _RUN, keys | {"completed_ago": STOPPED_FOR, "expected_ago": a_day}
            )
            stopped = await metrics.read_queues(connection, now=now)
            await connection.execute(
                text(
                    "UPDATE detection_project_runs SET expected_since = NULL "
                    "WHERE project_id = :project"
                ),
                keys,
            )
            no_longer_expected = await metrics.read_queues(connection, now=now)
        finally:
            await transaction.rollback()

    assert judged_on_time.detection_projects_behind == before.detection_projects_behind
    assert judged_on_time.detection_oldest_lag_seconds >= (JUST_RAN - CLOCKS_APART).total_seconds()
    assert stopped.detection_projects_behind == before.detection_projects_behind + 1
    assert stopped.detection_oldest_lag_seconds >= (STOPPED_FOR - CLOCKS_APART).total_seconds()
    assert no_longer_expected.detection_projects_behind == before.detection_projects_behind


async def test_the_settled_minute_the_sweep_records_is_read_as_its_age(
    engine: AsyncEngine,
) -> None:
    """*Detection stalled*'s reading. Before any sweep has settled a minute it is
    infinitely old, so a scheduler whose sweep never ran is not read as healthy."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text("DELETE FROM detection_sweep"))
            never = await metrics.read_queues(connection, now=datetime.now(UTC))
            await coordinator.record_sweep(connection, None)
            settled_nothing = await metrics.read_queues(connection, now=datetime.now(UTC))
            stalled_at = await connection.scalar(
                text("SELECT date_trunc('minute', now()) - CAST(:stalled AS interval)"),
                {"stalled": STALLED_FOR},
            )
            await coordinator.record_sweep(connection, stalled_at)
            stalled = await metrics.read_queues(connection, now=datetime.now(UTC))
            # A minute held back by nexovue's intake never moves the record backwards.
            await coordinator.record_sweep(connection, stalled_at - JUST_RAN)
            await coordinator.record_sweep(connection, None)
            still = await metrics.read_queues(connection, now=datetime.now(UTC))
        finally:
            await transaction.rollback()

    assert never.detection_settled_minute_age_seconds == math.inf
    assert settled_nothing.detection_settled_minute_age_seconds == math.inf
    stalled_seconds = STALLED_FOR.total_seconds()
    assert stalled_seconds <= stalled.detection_settled_minute_age_seconds < stalled_seconds + 60
    assert (
        still.detection_settled_minute_age_seconds == stalled.detection_settled_minute_age_seconds
    )


async def test_detection_events_of_the_last_fifteen_minutes_are_counted_by_kind(
    engine: AsyncEngine,
) -> None:
    """*Detection held by intake*'s reading. Beat reads the rows: the sweep that
    writes them runs in a worker's child process, whose metrics no scrape sees."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            before = await metrics.read_queues(connection, now=datetime.now(UTC))
            for kind, ago in (
                (holds.HOLD_EXPIRED, timedelta(minutes=1)),
                (holds.GUARD_ENGAGED, timedelta(minutes=20)),
            ):
                await connection.execute(
                    text(
                        "INSERT INTO detection_events (at, kind, detail) "
                        "VALUES (now() - CAST(:ago AS interval), :kind, '{}')"
                    ),
                    {"ago": ago, "kind": kind},
                )
            after = await metrics.read_queues(connection, now=datetime.now(UTC))
        finally:
            await transaction.rollback()

    assert set(after.detection_events_recent) == set(holds.EVENT_KINDS)
    expired, guarded = holds.HOLD_EXPIRED, holds.GUARD_ENGAGED
    assert after.detection_events_recent[expired] == before.detection_events_recent[expired] + 1
    assert after.detection_events_recent[guarded] == before.detection_events_recent[guarded]

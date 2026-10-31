"""The owners are told when detection stops judging a project, and when it runs
again (plan T4.6; spec FR25, AC43; D24).

The stop sweep runs in `sweep:detection` every minute, whatever the pipeline says;
*running again* is written by the first project job that completes after it.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
from datetime import datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

from alert_fixtures import AlertTenant, future, postgres_reachable, rows
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.detection import notify
from nexovue_scheduler.detection.project_job import judge_project
from nexovue_scheduler.detection.store import connect

READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
WRITER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DETECTION_WRITER_DSN",
    "clickhouse://nexovue_detection_writer:nexovue_detection_writer_dev@localhost:18123/nexovue",
)


def _clickhouse_reachable() -> bool:
    parts = urlsplit(READER_DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 8123), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=postgres_reachable() and _clickhouse_reachable(),
    suite="apps/scheduler/tests/test_detection_stopped_live.py",
    remedy="Postgres or ClickHouse is not reachable — run `just up`",
)


async def _last_run(
    engine: AsyncEngine, tenant: AlertTenant, at: datetime, *, sending: bool = True
) -> None:
    """The project's last completed run at `at`; `sending` gives it traffic since,
    and the sweep has expected it to be judged since then too."""
    async with engine.begin() as connection:
        keys = {"org_id": tenant.org_id, "project_id": tenant.project_id}
        await connection.execute(
            text(
                """
                INSERT INTO detection_project_runs
                    (org_id, project_id, last_completed_at, expected_since)
                VALUES (:org_id, :project_id, :at, :expected_since)
                ON CONFLICT (org_id, project_id) DO UPDATE
                SET last_completed_at = EXCLUDED.last_completed_at,
                    expected_since = coalesce(
                        detection_project_runs.expected_since, EXCLUDED.expected_since
                    )
                """
            ),
            keys | {"at": at, "expected_since": at if sending else None},
        )
    if sending:
        await _sends(engine, tenant, at)


async def _sends(engine: AsyncEngine, tenant: AlertTenant, at: datetime) -> None:
    """The usage meter saw the project's traffic at `at`."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO service_last_seen (org_id, project_id, service_name, last_seen_at)
                VALUES (:org_id, :project_id, 'checkout', :at)
                ON CONFLICT (project_id, service_name) DO UPDATE
                SET last_seen_at = EXCLUDED.last_seen_at
                """
            ),
            {"org_id": tenant.org_id, "project_id": tenant.project_id, "at": at},
        )


async def _report(engine: AsyncEngine, tenant: AlertTenant, now: datetime) -> int:
    """This tenant's only: the database is shared with the running stack."""
    async with engine.begin() as connection:
        return await notify.report_stopped(connection, now=now, org_id=tenant.org_id)


async def _told(engine: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    return await rows(
        engine,
        "SELECT kind, severity, channel_id, sequence, incident_id, finding_id "
        "FROM notifications WHERE org_id = :org_id ORDER BY created_at, kind",
        org_id=tenant.org_id,
    )


async def _reported_at(engine: AsyncEngine, tenant: AlertTenant) -> datetime | None:
    [run] = await rows(
        engine,
        "SELECT stopped_reported_at FROM detection_project_runs WHERE project_id = :project_id",
        project_id=tenant.project_id,
    )
    reported: datetime | None = run.stopped_reported_at
    return reported


async def test_fifteen_minutes_without_a_run_emails_the_owners_once(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC43, D24: once, to the owners channel, with the project as its subject."""
    now = future()
    await _last_run(engine, tenant, now - timedelta(minutes=16))

    assert await _report(engine, tenant, now) == 1
    assert await _report(engine, tenant, now + timedelta(minutes=5)) == 0

    [stopped] = await _told(engine, tenant)
    assert (stopped.kind, stopped.severity, stopped.channel_id) == (
        "stopped",
        notify.STOPPED_SEVERITY,
        tenant.owners_channel_id,
    )
    assert (stopped.incident_id, stopped.finding_id) == (None, None)
    assert await _reported_at(engine, tenant) == now


async def test_a_project_judged_on_time_is_never_reported(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC43's second half: a healthy project, judged within the last 15 minutes."""
    now = future()
    await _last_run(engine, tenant, now - timedelta(minutes=14))
    assert await _report(engine, tenant, now) == 0
    assert await _told(engine, tenant) == []


async def test_a_project_detection_no_longer_works_on_is_not_stopped(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """No traffic for a day and no finding open: the coordinator hands it no job, and
    that is not detection stopping."""
    now = future()
    await _last_run(engine, tenant, now - timedelta(hours=2), sending=False)
    assert await _report(engine, tenant, now) == 0


async def test_the_next_completed_run_says_running_again_once_and_a_new_stop_is_told_again(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """D24: one *running again* when it runs, and a later stop is a new message."""
    store = connect(READER_DSN, WRITER_DSN)
    now = future()
    await _last_run(engine, tenant, now - timedelta(minutes=20))
    await _report(engine, tenant, now)
    settled = now.replace(second=0, microsecond=0) - timedelta(minutes=3)

    running = now + timedelta(minutes=1)
    first = await judge_project(engine, store, tenant.project_id, settled=settled, now=running)
    second = await judge_project(engine, store, tenant.project_id, settled=settled, now=running)

    assert (first.notified, second.notified) == (1, 0)
    assert await _reported_at(engine, tenant) is None
    assert [row.kind for row in await _told(engine, tenant)] == ["stopped", "running_again"]

    later = now + timedelta(hours=1)
    await _last_run(engine, tenant, later - timedelta(minutes=16))
    assert await _report(engine, tenant, later) == 1
    stops = [row for row in await _told(engine, tenant) if row.kind == "stopped"]
    assert [row.sequence for row in stops] == [0, 1]


async def test_a_project_stopping_more_often_than_a_small_integer_counts_is_still_told(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """Each stop is the next in its sequence, and notifications are kept: the column
    must count past 32,767, or the owners stop hearing about a project that keeps
    stopping."""
    now = future()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO notifications
                    (org_id, project_id, channel_id, kind, sequence, severity, state,
                     deliver_after, created_at)
                VALUES (:org_id, :project_id, :channel_id, 'stopped', 32767, 'critical',
                        'delivered', :at, :at)
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "channel_id": tenant.owners_channel_id,
                "at": now - timedelta(days=1),
            },
        )
    await _last_run(engine, tenant, now - timedelta(minutes=16))

    assert await _report(engine, tenant, now) == 1
    stops = [row.sequence for row in await _told(engine, tenant) if row.kind == "stopped"]
    assert stops == [32767, 32768]


# --------------------------------------------- a project whose detection never ran (D34)


async def test_a_project_with_traffic_whose_detection_never_ran_is_told_after_its_chance(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """D34, AC43: a fresh install whose detection cannot judge anything. The 15
    minutes count from the first sweep that expected a run, never from the
    project's creation, so its first minutes of traffic are its chance to be judged."""
    first_seen = future()
    await _sends(engine, tenant, first_seen)

    assert await _report(engine, tenant, first_seen) == 0
    assert await _report(engine, tenant, first_seen + timedelta(minutes=14)) == 0
    assert await _report(engine, tenant, first_seen + timedelta(minutes=16)) == 1
    assert await _report(engine, tenant, first_seen + timedelta(minutes=30)) == 0

    [stopped] = await _told(engine, tenant)
    assert (stopped.kind, stopped.severity, stopped.channel_id) == (
        "stopped",
        notify.STOPPED_SEVERITY,
        tenant.owners_channel_id,
    )


async def test_a_never_run_project_s_first_completed_run_says_running_again_once(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """D24 for D34's stop: one *running again*, after the first completed run."""
    store = connect(READER_DSN, WRITER_DSN)
    first_seen = future()
    await _sends(engine, tenant, first_seen)
    await _report(engine, tenant, first_seen)
    told_at = first_seen + timedelta(minutes=16)
    await _report(engine, tenant, told_at)
    settled = told_at.replace(second=0, microsecond=0) - timedelta(minutes=3)

    running = told_at + timedelta(minutes=1)
    first = await judge_project(engine, store, tenant.project_id, settled=settled, now=running)
    second = await judge_project(engine, store, tenant.project_id, settled=settled, now=running)

    assert (first.notified, second.notified) == (1, 0)
    assert [row.kind for row in await _told(engine, tenant)] == ["stopped", "running_again"]


async def test_a_project_without_traffic_whose_detection_never_ran_stays_silent(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """Nothing to judge is not detection stopping."""
    now = future()
    assert await _report(engine, tenant, now) == 0
    assert await _report(engine, tenant, now + timedelta(hours=1)) == 0
    assert await _told(engine, tenant) == []


async def test_a_project_sending_again_after_a_quiet_day_has_its_chance_before_it_counts(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """Its last run is a day old because nothing was sent, not because detection
    stopped: the clock starts again when the sweep next expects it to be judged."""
    now = future()
    await _last_run(engine, tenant, now - timedelta(days=2), sending=False)
    await _sends(engine, tenant, now - timedelta(days=2))
    assert await _report(engine, tenant, now - timedelta(hours=1)) == 0

    await _sends(engine, tenant, now)
    assert await _report(engine, tenant, now) == 0
    assert await _report(engine, tenant, now + timedelta(minutes=14)) == 0
    assert await _report(engine, tenant, now + timedelta(minutes=16)) == 1

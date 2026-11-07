"""F19's jobs against real Postgres and ClickHouse (plan T1.5).

AC8 through the hand-out: a stopped service reads **No recent requests** with its
silence's length, never **On track**, after 16 minutes, 25 hours, 3, 10 and 27 days,
at figures of 100% and 99.99%. AC64's history loop and crash recovery, AC65's
longest-waiting order, AC66's overlapping and older runs, and AC48's stale status.

The jobs `queue_jobs` hands out are read back from `jobs` and run by name, as a worker
would. This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest
from alert_fixtures import AlertTenant
from clickhouse_connect.driver.client import Client
from live import require_stack
from nexovue_platform import slo
from slo_rows import minutes, write_service
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.slos import job
from nexovue_scheduler.slos.store import SloStore, connect_store

READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
SERVICE: Final = "checkout"
UTC_ZONE: Final = ZoneInfo("UTC")
MINUTE: Final = timedelta(minutes=1)


def _reachable(url: str, port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or port), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(READER_DSN, 8123) and _reachable(POSTGRES_DSN, 5432),
    suite="F19's job suite",
    remedy="start the dev stack with `just dev`",
)


@pytest.fixture
def slo_store() -> SloStore:
    return connect_store(READER_DSN)


@pytest.fixture
async def clean_jobs(engine: AsyncEngine, project: AlertTenant) -> AsyncIterator[None]:
    yield
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM jobs WHERE org_id = :org_id"), {"org_id": project.org_id}
        )


async def _target(
    engine: AsyncEngine,
    tenant: AlertTenant,
    *,
    objective: str = "99.9",
    definition_version: int = 1,
    history_read_version: int = 0,
) -> uuid.UUID:
    key = slo.definition_key(
        slo.SERVICE_AVAILABILITY,
        service_name=SERVICE,
        objective=Decimal(objective),
        window_days=28,
    )
    async with engine.begin() as connection:
        made = await connection.scalar(
            text(
                """
                INSERT INTO slo_targets (org_id, project_id, name, kind, service_name,
                    objective_percent, window_days, upstream_sampling, definition_key,
                    definition_version, history_read_version)
                VALUES (:org_id, :project_id, :name, 'service_availability', :service,
                    :objective, 28, 'no', :key, :definition, :read)
                RETURNING id
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "name": f"checkout availability {objective}",
                "service": SERVICE,
                "objective": Decimal(objective),
                "key": key,
                "definition": definition_version,
                "read": history_read_version,
            },
        )
    assert isinstance(made, uuid.UUID)
    return made


async def _run_queued(
    engine: AsyncEngine, store: SloStore, tenant: AlertTenant, *, now: datetime
) -> list[str]:
    """Run, by name and in claim order, every job `queue_jobs` queued for this tenant:
    history first, as its own queue would finish it before the next minute's judging."""
    async with engine.begin() as connection:
        rows = list(
            await connection.execute(
                text("DELETE FROM jobs WHERE org_id = :org_id RETURNING task, kwargs, run_after"),
                {"org_id": tenant.org_id},
            )
        )
    order = {slo.READ_SLO_HISTORY_TASK: 0, slo.COUNT_SLOS_TASK: 1, slo.JUDGE_SLOS_TASK: 2}
    ran = []
    for row in sorted(rows, key=lambda row: (order[row.task], row.run_after)):
        kwargs: dict[str, Any] = row.kwargs
        if row.task == slo.READ_SLO_HISTORY_TASK:
            await job.read_slo_history(engine, store, uuid.UUID(kwargs["target_id"]), now=now)
        elif row.task == slo.COUNT_SLOS_TASK:
            await job.count_slos(engine, store, uuid.UUID(kwargs["project_id"]), now=now)
        else:
            settled = datetime.fromisoformat(kwargs["settled_minute"])
            await job.judge_slos(engine, store, uuid.UUID(kwargs["project_id"]), settled)
        ran.append(row.task)
    return ran


async def _status(engine: AsyncEngine, target_id: uuid.UUID, *, now: datetime) -> slo.Status:
    """FR10's state as the list will compute it: from Postgres alone."""
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT t.kind, t.objective_percent, t.window_days, t.enabled,
                           t.first_event_at, t.definition_version, t.history_read_version,
                           t.created_at,
                           s.evaluated_minute, s.last_event_minute, s.longest_quiet_7d
                    FROM slo_targets t LEFT JOIN slo_status s ON s.slo_id = t.id
                    WHERE t.id = :id
                    """
                ),
                {"id": target_id},
            )
        ).one()
        days = {
            day.day: slo.DayCounts(day.good, day.total, day.lost_minutes)
            for day in await connection.execute(
                text(
                    "SELECT day, good, total, lost_minutes FROM slo_daily_counts WHERE slo_id = :id"
                ),
                {"id": target_id},
            )
        }
    stored = slo.StoredTarget(
        kind=row.kind,
        objective=row.objective_percent,
        window_days=row.window_days,
        enabled=row.enabled,
        first_event_at=row.first_event_at,
        definition_version=row.definition_version,
        history_read_version=row.history_read_version,
        source_missing=False,
        evaluated_minute=row.evaluated_minute,
        last_event_minute=row.last_event_minute,
        longest_quiet_7d_minutes=row.longest_quiet_7d or 0,
        created_at=row.created_at,
    )
    facts, _ = slo.facts_from_storage(stored, days, now=now, zone=UTC_ZONE)
    return slo.status_of(facts)


def _now() -> datetime:
    return datetime.now(UTC).replace(second=0, microsecond=0)


# ------------------------------------------------------------------------- AC8


@pytest.mark.parametrize(
    "silence",
    [
        timedelta(minutes=16),
        timedelta(hours=25),
        timedelta(days=3),
        timedelta(days=10),
        timedelta(days=27),
    ],
    ids=["16m", "25h", "3d", "10d", "27d"],
)
@pytest.mark.parametrize(("failures", "figure"), [(0, "100%"), (1, "99.99%")])
async def test_ac8_a_stopped_service_reads_no_recent_requests_never_on_track(
    engine: AsyncEngine,
    administrator: Client,
    project: AlertTenant,
    slo_store: SloStore,
    clean_jobs: None,
    silence: timedelta,
    failures: int,
    figure: str,
) -> None:
    now = _now()
    last = now - silence - MINUTE
    busy = minutes(last - timedelta(minutes=119), 120)
    # 10,000 requests over two hours, with the one failure that makes 99.99%.
    counts = {minute: (84 if index < 40 else 83, 0) for index, minute in enumerate(busy)}
    counts[busy[0]] = (counts[busy[0]][0], failures)
    write_service(administrator, project, SERVICE, counts)
    target = await _target(engine, project)

    judged = await job.queue_jobs(engine, now - MINUTE, now=now)
    ran = await _run_queued(engine, slo_store, project, now=now)
    # The next minute: history is read, so its judging finds the stored days.
    await job.queue_jobs(engine, now, now=now + MINUTE)
    ran += await _run_queued(engine, slo_store, project, now=now + MINUTE)

    assert judged >= 1 and slo.JUDGE_SLOS_TASK in ran
    status = await _status(engine, target, now=now + MINUTE)
    assert status.state is slo.State.NO_RECENT_REQUESTS, (figure, status)
    assert status.quiet_minutes is not None
    assert abs(status.quiet_minutes - (silence + 2 * MINUTE) / MINUTE) <= 1


# ------------------------------------------------------------------------ AC66


async def test_ac66_an_older_minute_never_overwrites_a_newer_status(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    now = _now()
    write_service(administrator, project, SERVICE, {now - 10 * MINUTE: (5, 0)})
    target = await _target(engine, project, history_read_version=1)

    assert await job.judge_slos(engine, slo_store, project.project_id, now) == 1
    assert await job.judge_slos(engine, slo_store, project.project_id, now - 5 * MINUTE) == 0
    assert await job.judge_slos(engine, slo_store, project.project_id, now) == 0

    async with engine.connect() as connection:
        evaluated = await connection.scalar(
            text("SELECT evaluated_minute FROM slo_status WHERE slo_id = :id"), {"id": target}
        )
    assert evaluated == now


async def test_ac66_two_overlapping_runs_do_not_both_judge_a_project(
    engine: AsyncEngine, project: AlertTenant, slo_store: SloStore
) -> None:
    await _target(engine, project, history_read_version=1)
    lock = "SELECT pg_advisory_lock(hashtextextended(:key, 0))"
    async with engine.connect() as holder:
        key = {"key": f"detection:slo-judge:{project.project_id}"}
        await holder.execute(text(lock), key)
        try:
            assert await job.judge_slos(engine, slo_store, project.project_id, _now()) == 0
        finally:
            await holder.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), key)


# ------------------------------------------------------------------------ AC64


async def test_ac64_a_crash_between_commit_and_queueing_is_picked_up_by_the_next_minute(
    engine: AsyncEngine, project: AlertTenant, clean_jobs: None
) -> None:
    target = await _target(engine, project, definition_version=2, history_read_version=1)
    await job.queue_jobs(engine, _now(), now=_now())
    async with engine.connect() as connection:
        queued = list(
            await connection.execute(
                text("SELECT task, kwargs FROM jobs WHERE org_id = :org_id"),
                {"org_id": project.org_id},
            )
        )
    reads = [row.kwargs for row in queued if row.task == slo.READ_SLO_HISTORY_TASK]
    assert reads == [{"target_id": str(target)}]


async def test_ac64_a_definition_change_during_a_read_reads_again_under_the_new_version(
    engine: AsyncEngine,
    administrator: Client,
    project: AlertTenant,
    slo_store: SloStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = _now()
    write_service(administrator, project, SERVICE, {now - timedelta(days=2): (9, 0)})
    target = await _target(engine, project)
    changed = False
    count_days = job._count_days

    async def change_once(connection: Any, *arguments: Any, **options: Any) -> int:
        """A person changes what is counted while the read is under way."""
        nonlocal changed
        if not changed:
            changed = True
            await connection.execute(
                text(
                    "UPDATE slo_targets SET definition_version = 2 "
                    "WHERE id = :id AND org_id = :org_id"
                ),
                {"id": target, "org_id": project.org_id},
            )
            await connection.commit()
        return await count_days(connection, *arguments, **options)

    monkeypatch.setattr(job, "_count_days", change_once)
    await job.read_slo_history(engine, slo_store, target, now=now)

    async with engine.connect() as connection:
        read, versions = (
            await connection.execute(
                text(
                    """
                    SELECT t.history_read_version,
                           array_agg(DISTINCT c.definition_version) AS versions
                    FROM slo_targets t JOIN slo_daily_counts c ON c.slo_id = t.id
                    WHERE t.id = :id GROUP BY t.history_read_version
                    """
                ),
                {"id": target},
            )
        ).one()
    assert read == 2
    assert versions == [2]


# ------------------------------------------------------------------------ AC65


async def test_ac65_the_project_waiting_longest_is_handed_out_first(
    engine: AsyncEngine, project: AlertTenant, clean_jobs: None, slo_store: SloStore
) -> None:
    waited = await _target(engine, project, history_read_version=1)
    now = _now()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO slo_status (slo_id, org_id, project_id, evaluated_minute) "
                "VALUES (:id, :org_id, :project_id, :minute)"
            ),
            {
                "id": waited,
                "org_id": project.org_id,
                "project_id": project.project_id,
                "minute": now - timedelta(days=3650),
            },
        )
    await job.queue_jobs(engine, now, now=now)
    async with engine.connect() as connection:
        first = (
            await connection.execute(
                text("SELECT org_id FROM jobs WHERE task = :task ORDER BY run_after, id LIMIT 1"),
                {"task": slo.JUDGE_SLOS_TASK},
            )
        ).one()
    # Ten years unjudged is longer than any other project's wait on this store.
    assert first.org_id == project.org_id


# ------------------------------------------------------------------------ AC48


async def test_ac48_status_the_job_stopped_writing_reads_not_up_to_date(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    now = _now()
    write_service(
        administrator,
        project,
        SERVICE,
        dict.fromkeys(minutes(now - 30 * MINUTE, 30), (10, 0)),
    )
    target = await _target(engine, project, history_read_version=1)
    await job.judge_slos(engine, slo_store, project.project_id, now)

    later = await _status(engine, target, now=now + 16 * MINUTE)
    assert later.state is slo.State.NOT_UP_TO_DATE

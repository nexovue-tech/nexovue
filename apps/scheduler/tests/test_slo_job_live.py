"""F19's jobs against real Postgres and ClickHouse (plan T1.5).

AC8 through the hand-out: a stopped service reads **No recent requests** with its
silence's length, never **On track**, after 16 minutes, 25 hours, 3, 10 and 27 days,
at figures of 100% and 99.99%. AC64's history loop and crash recovery, AC65's
longest-waiting order, AC66's overlapping and older runs, and AC48's stale status.

Step 3 (T3.2): AC7's final days, rewritten only by a definition change inside the
recount limit and marked changed; AC45's older days kept under the old definition;
AC69's day beyond the limit untouched and a day first read past 29 days marked loss
not known; and AC64's last clause with a final day inside the limit.

Step 4 (T4.5, T4.6): a burn judged under its rules through the real job: AC17 (resolves
10 minutes after no tier holds), AC18 (two targets on one service, two incidents), AC19
(a slow burn's warning escalates once, with current figures), AC20 (lost minutes judge
nothing), AC21 (a restart resumes the resolving wait), AC22 (one *Nothing left* warning
to a channel two rules name, and its repeat limits), AC23 (no second incident or row),
AC62 (the warning waits while building history), AC93 (a burn through a silence resolves
once judging resumes), AC94's closing (a stopped source closes a burn after a measured
day, and says so), and AC104 (a lower rate without a severity change).

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
from alert_fixtures import AlertTenant, add_rule
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


# ------------------------------------------------------------- Step 3: recounts


async def _on_a_paid_plan(engine: AsyncEngine, tenant: AlertTenant) -> None:
    """395 days of derived retention, so a service target reaches its 35 days of rollups
    and a day can sit inside them and past the 29-day recount limit."""
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE organizations SET plan = 'pro' WHERE id = :org_id"),
            {"org_id": tenant.org_id},
        )


async def _store_day(
    engine: AsyncEngine,
    tenant: AlertTenant,
    target: uuid.UUID,
    day: Any,
    *,
    total: int,
    version: int,
    final: bool,
) -> None:
    """A day as an earlier count left it: `total` requests, none failing."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO slo_daily_counts (org_id, slo_id, day, good, total, failures,
                    quiet_minutes, lost_minutes, quiet_leading_minutes, quiet_trailing_minutes,
                    longest_quiet_minutes, definition_version, final, computed_at)
                VALUES (:org_id, :id, :day, :total, :total, 0, 0, 0, 0, 0, 0, :version,
                        :final, now() - interval '30 days')
                """
            ),
            {
                "org_id": tenant.org_id,
                "id": target,
                "day": day,
                "total": total,
                "version": version,
                "final": final,
            },
        )


async def _days(engine: AsyncEngine, target: uuid.UUID) -> dict[Any, Any]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT day, total, definition_version, final, loss_known, changed_at, "
                "changed_why FROM slo_daily_counts WHERE slo_id = :id"
            ),
            {"id": target},
        )
        return {row.day: row for row in rows}


async def _change_what_is_counted(engine: AsyncEngine, target: uuid.UUID, version: int) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE slo_targets SET definition_version = :version WHERE id = :id"),
            {"id": target, "version": version},
        )


async def test_ac7_a_final_day_is_rewritten_only_by_a_definition_change_inside_the_limit(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    now = _now()
    today = now.date()
    inside, beyond = today - timedelta(days=10), today - timedelta(days=32)
    await _on_a_paid_plan(engine, project)
    write_service(
        administrator,
        project,
        SERVICE,
        {
            datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=12): (40, 0)
            for day in (inside, beyond, today - timedelta(days=34))
        },
    )
    target = await _target(engine, project, history_read_version=1)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE slo_targets SET first_event_at = :first WHERE id = :id"),
            {"id": target, "first": now - timedelta(days=40)},
        )
    await _store_day(engine, project, target, inside, total=7, version=1, final=True)
    await _store_day(engine, project, target, beyond, total=7, version=1, final=True)

    await job.count_slos(engine, slo_store, project.project_id, now=now)
    assert (await _days(engine, target))[inside].total == 7

    await _change_what_is_counted(engine, target, 2)
    await job.read_slo_history(engine, slo_store, target, now=now)
    days = await _days(engine, target)

    assert (days[inside].total, days[inside].definition_version) == (40, 2)
    assert days[inside].final is True
    assert days[inside].changed_why == slo.CHANGED_BY_DEFINITION
    assert days[inside].changed_at is not None
    # AC45, AC69: past the limit a day stays as counted, under the old definition.
    assert (days[beyond].total, days[beyond].definition_version) == (7, 1)
    assert days[beyond].changed_at is None
    # AC69: a day first read past the 29 days nexovue's loss records last.
    first_read = days[today - timedelta(days=34)]
    assert (first_read.total, first_read.loss_known) == (40, False)
    assert days[inside].loss_known is True


async def test_a_recount_that_changes_nothing_marks_no_day_changed(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    now = _now()
    inside = now.date() - timedelta(days=10)
    write_service(
        administrator,
        project,
        SERVICE,
        {datetime.combine(inside, datetime.min.time(), UTC) + timedelta(hours=12): (40, 0)},
    )
    target = await _target(engine, project, history_read_version=1)
    await _store_day(engine, project, target, inside, total=40, version=1, final=True)

    await _change_what_is_counted(engine, target, 2)
    await job.read_slo_history(engine, slo_store, target, now=now)
    day = (await _days(engine, target))[inside]

    assert (day.total, day.definition_version, day.changed_at) == (40, 2, None)


async def test_a_count_never_writes_an_older_definition_over_a_newer_day(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    now = _now()
    write_service(administrator, project, SERVICE, {now - 30 * MINUTE: (40, 0)})
    target = await _target(engine, project, history_read_version=1)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE slo_targets SET first_event_at = :first WHERE id = :id"),
            {"id": target, "first": now - timedelta(days=1)},
        )
    # A recount under version 3 landed while this count still held version 1.
    await _store_day(engine, project, target, now.date(), total=9, version=3, final=False)

    await job.count_slos(engine, slo_store, project.project_id, now=now)

    day = (await _days(engine, target))[now.date()]
    assert (day.total, day.definition_version) == (9, 3)


async def test_ac64_a_change_during_a_read_recounts_final_days_inside_the_limit_too(
    engine: AsyncEngine,
    administrator: Client,
    project: AlertTenant,
    slo_store: SloStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = _now()
    inside = now.date() - timedelta(days=10)
    write_service(
        administrator,
        project,
        SERVICE,
        {datetime.combine(inside, datetime.min.time(), UTC) + timedelta(hours=12): (40, 0)},
    )
    target = await _target(engine, project)
    await _store_day(engine, project, target, inside, total=7, version=1, final=True)
    changed = False
    count_days = job._count_days

    async def change_once(connection: Any, *arguments: Any, **options: Any) -> int:
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

    days = await _days(engine, target)
    assert {day.definition_version for day in days.values()} == {2}
    assert days[inside].total == 40


# ===================================================================== Step 4: burns
#
# Plan T4.5 and T4.6: a burn through the real `judge_slos`, judged under each rule, with
# rows written to `service_stats_1m` by the minute. 100 requests a minute for a day,
# then failures where a test wants them.

BUSY: Final = 100


async def _burn_rule(
    engine: AsyncEngine,
    tenant: AlertTenant,
    *,
    name: str = "Reliability targets",
    condition: dict[str, Any] | None = None,
    channel: uuid.UUID | None = None,
) -> uuid.UUID:
    async with engine.begin() as connection:
        return await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name=name,
            condition=condition or {"kind": "slo_burn"},
            channel_ids=[channel or tenant.owners_channel_id],
        )


async def _counted_target(
    engine: AsyncEngine, tenant: AlertTenant, *, objective: str = "99.9", window: int = 28
) -> uuid.UUID:
    """A target whose history is read: it judges from its first minute."""
    key = slo.definition_key(
        slo.SERVICE_AVAILABILITY,
        service_name=SERVICE,
        objective=Decimal(objective),
        window_days=window,
    )
    async with engine.begin() as connection:
        made = await connection.scalar(
            text(
                """
                INSERT INTO slo_targets (org_id, project_id, name, kind, service_name,
                    objective_percent, window_days, upstream_sampling, definition_key,
                    definition_version, history_read_version, first_event_at)
                VALUES (:org_id, :project_id, :name, 'service_availability', :service,
                    :objective, :window, 'no', :key, 1, 1, now() - interval '30 days')
                RETURNING id
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "name": f"checkout {objective} {window}",
                "service": SERVICE,
                "objective": Decimal(objective),
                "window": window,
                "key": key,
            },
        )
    assert isinstance(made, uuid.UUID)
    return made


def _traffic(
    client: Client,
    tenant: AlertTenant,
    *,
    first: datetime,
    count: int,
    failing: dict[datetime, int] | None = None,
) -> None:
    counts = dict.fromkeys(minutes(first, count), (BUSY, 0))
    for minute, failures in (failing or {}).items():
        counts[minute] = (BUSY, failures)
    write_service(client, tenant, SERVICE, counts)


# The incidents `judge_slos` writes are about one target each (`slo:<id>`, its burn and
# its notices). The dev stack's delivery sweep, which CI runs beside this suite, also
# files a project's *not being judged* notice under `slo_burn` (`slo-project:<id>:blind`,
# FR13b): rightly, since these tests judge minutes hours old. It is not this suite's
# subject (`test_slo_blind_live.py` is), so each read here leaves it out.
_ABOUT_A_TARGET: Final = "subject_key LIKE 'slo:%'"


async def _incidents(engine: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            await connection.execute(
                text(
                    f"""
                    SELECT id, subject_key, subject_label, severity, resolved_at, detail,
                           rule_name, is_event
                    FROM incidents
                    WHERE org_id = :org_id AND condition_kind = 'slo_burn'
                      AND {_ABOUT_A_TARGET}
                    ORDER BY opened_at, id
                    """
                ),
                {"org_id": tenant.org_id},
            )
        )


async def _notifications(engine: AsyncEngine, tenant: AlertTenant) -> list[tuple[str, str]]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                f"""
                SELECT n.kind, n.severity FROM notifications n
                JOIN incidents i ON i.id = n.incident_id
                WHERE n.org_id = :org_id AND i.condition_kind = 'slo_burn'
                  AND i.{_ABOUT_A_TARGET}
                ORDER BY n.created_at, n.id
                """
            ),
            {"org_id": tenant.org_id},
        )
        return [(row.kind, row.severity) for row in rows]


def _fast_burn(start: datetime) -> dict[datetime, int]:
    """Half of five minutes' requests failing, ending at `start`: fast and medium hold."""
    return {start - MINUTE * offset: BUSY // 2 for offset in range(5)}


async def test_ac17_a_fast_burn_fires_critical_and_resolves_ten_minutes_after_no_tier_holds(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    burning = _now() - timedelta(hours=2)
    _traffic(
        administrator,
        project,
        first=burning - timedelta(hours=25),
        count=26 * 60,
        failing=_fast_burn(burning),
    )
    await _burn_rule(engine, project)
    target = await _counted_target(engine, project)

    await job.judge_slos(engine, slo_store, project.project_id, burning)
    [opened] = await _incidents(engine, project)
    assert (opened.severity, opened.resolved_at) == ("critical", None)
    assert opened.subject_key == f"slo:{target}"
    assert opened.subject_label == "checkout availability, 99.9% over 28 days"
    # The medium tier's 30-minute short window still holds the failures until +30.
    for offset in (29, 30, 39):
        await job.judge_slos(engine, slo_store, project.project_id, burning + MINUTE * offset)
    [still] = await _incidents(engine, project)
    assert still.resolved_at is None
    await job.judge_slos(engine, slo_store, project.project_id, burning + MINUTE * 40)
    [resolved] = await _incidents(engine, project)
    assert resolved.resolved_at == burning + MINUTE * 40
    assert await _notifications(engine, project) == [
        ("opened", "critical"),
        ("resolved", "critical"),
    ]


async def test_ac18_two_targets_on_one_service_under_one_rule_hold_two_incidents(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    burning = _now() - timedelta(hours=2)
    _traffic(
        administrator,
        project,
        first=burning - timedelta(hours=25),
        count=25 * 60 + 1,
        failing=_fast_burn(burning),
    )
    await _burn_rule(engine, project)
    first = await _counted_target(engine, project, window=28)
    second = await _counted_target(engine, project, window=7)

    await job.judge_slos(engine, slo_store, project.project_id, burning)
    opened = await _incidents(engine, project)
    assert {row.subject_key for row in opened} == {f"slo:{first}", f"slo:{second}"}
    assert all(row.resolved_at is None for row in opened)


async def test_ac19_a_slow_burn_opens_a_warning_and_escalates_once_through_the_job(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    """0.5% failing for a day: only the slow tier holds. Then half failing for five
    minutes: the fast tier holds, and the one incident escalates with one row."""
    slow = _now() - timedelta(hours=2)
    first = slow - timedelta(hours=25)
    steady = {minute: 1 for minute in minutes(first, 26 * 60) if minute.minute % 2 == 0}
    later = slow + MINUTE * 5
    _traffic(administrator, project, first=first, count=26 * 60, failing=steady | _fast_burn(later))
    await _burn_rule(engine, project)
    await _counted_target(engine, project)

    await job.judge_slos(engine, slo_store, project.project_id, slow)
    [opened] = await _incidents(engine, project)
    assert opened.severity == "warning"
    assert opened.detail["tier"] == "slow"
    slow_rate = opened.detail["rate"]

    await job.judge_slos(engine, slo_store, project.project_id, later)
    await job.judge_slos(engine, slo_store, project.project_id, later + MINUTE)
    [escalated] = await _incidents(engine, project)
    assert escalated.id == opened.id
    assert escalated.severity == "critical"
    # The figures are this minute's, not the opening's (AC19, AC104).
    assert escalated.detail["tier"] == "fast"
    assert escalated.detail["rate"] > slow_rate
    assert escalated.detail["as_of"] == (later + MINUTE).isoformat()
    assert await _notifications(engine, project) == [
        ("opened", "warning"),
        ("escalated", "critical"),
    ]


async def test_ac104_a_burn_falling_without_a_severity_change_shows_the_lower_rate(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    """Five minutes on, the fast tier's short window is clean and the medium tier holds
    alone: still critical, at its lower rate."""
    burning = _now() - timedelta(hours=2)
    _traffic(
        administrator,
        project,
        first=burning - timedelta(hours=25),
        count=26 * 60,
        failing=_fast_burn(burning),
    )
    await _burn_rule(engine, project)
    await _counted_target(engine, project)

    await job.judge_slos(engine, slo_store, project.project_id, burning)
    [opened] = await _incidents(engine, project)
    await job.judge_slos(engine, slo_store, project.project_id, burning + 5 * MINUTE)
    [later] = await _incidents(engine, project)
    assert (opened.detail["tier"], later.detail["tier"]) == ("fast", "medium")
    assert later.severity == opened.severity == "critical"
    assert later.detail["rate"] < opened.detail["rate"]


async def test_ac23_a_repeated_or_restarted_judging_writes_no_second_incident_or_row(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    burning = _now() - timedelta(hours=2)
    _traffic(
        administrator,
        project,
        first=burning - timedelta(hours=25),
        count=26 * 60,
        failing=_fast_burn(burning) | _fast_burn(burning + 3 * MINUTE),
    )
    await _burn_rule(engine, project)
    await _counted_target(engine, project)

    await job.judge_slos(engine, slo_store, project.project_id, burning)
    assert await job.judge_slos(engine, slo_store, project.project_id, burning) == 0
    await job.judge_slos(engine, slo_store, project.project_id, burning + MINUTE)
    await job.judge_slos(engine, slo_store, project.project_id, burning + 2 * MINUTE)
    assert len(await _incidents(engine, project)) == 1
    assert await _notifications(engine, project) == [("opened", "critical")]


async def test_ac21_a_restart_in_the_resolving_wait_resumes_it(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    """The wait is the position in Postgres, so a new process continues it: judged at
    +30 (first minute not holding), then by a fresh store at +40, it resolves at +40."""
    burning = _now() - timedelta(hours=2)
    _traffic(
        administrator,
        project,
        first=burning - timedelta(hours=25),
        count=26 * 60,
        failing=_fast_burn(burning),
    )
    await _burn_rule(engine, project)
    await _counted_target(engine, project)
    await job.judge_slos(engine, slo_store, project.project_id, burning)
    await job.judge_slos(engine, slo_store, project.project_id, burning + MINUTE * 30)

    restarted = connect_store(READER_DSN)
    await job.judge_slos(engine, restarted, project.project_id, burning + MINUTE * 40)
    [resolved] = await _incidents(engine, project)
    assert resolved.resolved_at == burning + MINUTE * 40


async def test_ac20_with_every_tier_short_of_coverage_a_burn_does_not_move(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    """Minutes nexovue lost judge nothing (FR17): no incident opens, the position does
    not move, and the status says since when no tier could judge (FR13b)."""
    burning = _now() - timedelta(hours=2)
    _traffic(
        administrator,
        project,
        first=burning - timedelta(hours=25),
        count=26 * 60,
        failing=_fast_burn(burning),
    )
    await _burn_rule(engine, project)
    target = await _counted_target(engine, project)
    lost = minutes(burning - timedelta(minutes=179), 180)
    async with engine.begin() as connection:
        for minute in lost:
            await connection.execute(
                text(
                    "INSERT INTO intake_refusals (minute, receiver_id, reason, count) "
                    "VALUES (:minute, 'f19-test', 'broker_unavailable', 1) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"minute": minute},
            )
    try:
        await job.judge_slos(engine, slo_store, project.project_id, burning)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM intake_refusals WHERE receiver_id = 'f19-test'")
            )
    assert await _incidents(engine, project) == []
    async with engine.connect() as connection:
        unjudged = await connection.scalar(
            text("SELECT unjudged_since FROM slo_status WHERE slo_id = :id"), {"id": target}
        )
        positions = await connection.scalar(
            text("SELECT count(*) FROM alert_evaluations WHERE subject_key = :key"),
            {"key": f"slo:{target}"},
        )
    assert unjudged == burning
    assert positions == 1  # held as insufficient, with no `evaluated_at` of a judgment


async def test_ac93_a_burn_open_through_a_silence_resolves_ten_minutes_after_judging_resumes(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    burning = _now() - timedelta(hours=23)
    _traffic(
        administrator,
        project,
        first=burning - timedelta(hours=25),
        count=25 * 60 + 1,
        failing=_fast_burn(burning),
    )
    resumed = burning + timedelta(hours=20)
    _traffic(administrator, project, first=resumed - timedelta(minutes=30), count=41)
    await _burn_rule(engine, project)
    await _counted_target(engine, project)

    await job.judge_slos(engine, slo_store, project.project_id, burning)
    await job.judge_slos(engine, slo_store, project.project_id, burning + timedelta(hours=10))
    [open_through] = await _incidents(engine, project)
    assert open_through.resolved_at is None
    await job.judge_slos(engine, slo_store, project.project_id, resumed)
    await job.judge_slos(engine, slo_store, project.project_id, resumed + MINUTE * 10)
    [resolved] = await _incidents(engine, project)
    assert resolved.resolved_at == resumed + MINUTE * 10


async def test_ac94_a_burn_on_a_stopped_source_closes_after_a_measured_day_and_says_so(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    burning = _now() - timedelta(hours=26)
    _traffic(
        administrator,
        project,
        first=burning - timedelta(hours=24),
        count=24 * 60 + 1,
        failing=_fast_burn(burning),
    )
    await _burn_rule(engine, project)
    await _counted_target(engine, project)

    await job.judge_slos(engine, slo_store, project.project_id, burning)
    await job.judge_slos(engine, slo_store, project.project_id, burning + timedelta(hours=23))
    [still] = await _incidents(engine, project)
    assert still.resolved_at is None
    closed_at = burning + timedelta(hours=24)
    await job.judge_slos(engine, slo_store, project.project_id, closed_at)
    [closed] = await _incidents(engine, project)
    assert closed.resolved_at == closed_at
    assert closed.detail["closed_why"] == "source_silent"
    # People told of it are told it ended (FR17), unlike a person closing it.
    assert await _notifications(engine, project) == [
        ("opened", "critical"),
        ("closed", "critical"),
    ]


async def test_ac22_nothing_left_sends_one_warning_to_a_channel_two_rules_name(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    """With a burn open at that moment. Then not again while it hovers at zero, nor
    within 7 days of the last, and again once back above 5% and run out again."""
    burning = _now() - timedelta(hours=2)
    _traffic(
        administrator,
        project,
        first=burning - timedelta(hours=25),
        count=26 * 60,
        failing=_fast_burn(burning),
    )
    await _burn_rule(engine, project, name="A")
    await _burn_rule(engine, project, name="B")
    target = await _counted_target(engine, project)
    today = burning.astimezone(UTC_ZONE).date()
    async with engine.begin() as connection:
        for offset in range(10):
            await connection.execute(
                text(
                    """
                    INSERT INTO slo_daily_counts (org_id, slo_id, day, good, total, failures,
                        quiet_minutes, lost_minutes, quiet_leading_minutes,
                        quiet_trailing_minutes, longest_quiet_minutes, definition_version,
                        final, computed_at)
                    VALUES (:org_id, :id, :day, 9980, 10000, 20, 0, 0, 0, 0, 0, 1, false, now())
                    """
                ),
                {"org_id": project.org_id, "id": target, "day": today - timedelta(days=offset)},
            )

    await job.judge_slos(engine, slo_store, project.project_id, burning)
    await job.judge_slos(engine, slo_store, project.project_id, burning + MINUTE)
    notices = [row for row in await _incidents(engine, project) if row.is_event]
    assert [row.subject_key for row in notices] == [f"slo:{target}:exhausted"]
    assert notices[0].rule_name == "A"
    assert ("opened", "warning") in await _notifications(engine, project)
    async with engine.connect() as connection:
        rows = await connection.scalar(
            text("SELECT count(*) FROM notifications WHERE incident_id = :id"),
            {"id": notices[0].id},
        )
    assert rows == 1

    # Back above 5%: re-armed, but within 7 days of the last nothing more is said.
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE slo_daily_counts SET good = total, failures = 0 WHERE slo_id = :id"),
            {"id": target},
        )
    await job.judge_slos(engine, slo_store, project.project_id, burning + 2 * MINUTE)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE slo_daily_counts SET good = 9980, failures = 20 WHERE slo_id = :id"),
            {"id": target},
        )
    await job.judge_slos(engine, slo_store, project.project_id, burning + 3 * MINUTE)
    assert len([row for row in await _incidents(engine, project) if row.is_event]) == 1
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE slo_targets SET exhausted_warned_at = now() - interval '8 days' "
                "WHERE id = :id"
            ),
            {"id": target},
        )
    await job.judge_slos(engine, slo_store, project.project_id, burning + 4 * MINUTE)
    assert len([row for row in await _incidents(engine, project) if row.is_event]) == 2


async def test_ac62_while_building_history_the_warning_waits_and_burns_still_judge(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    burning = _now() - timedelta(hours=2)
    _traffic(
        administrator,
        project,
        first=burning - timedelta(hours=25),
        count=26 * 60,
        failing=_fast_burn(burning),
    )
    await _burn_rule(engine, project)
    target = await _counted_target(engine, project)
    today = burning.astimezone(UTC_ZONE).date()
    async with engine.begin() as connection:
        for offset in range(2):  # 2 of 28 days held
            await connection.execute(
                text(
                    """
                    INSERT INTO slo_daily_counts (org_id, slo_id, day, good, total, failures,
                        quiet_minutes, lost_minutes, quiet_leading_minutes,
                        quiet_trailing_minutes, longest_quiet_minutes, definition_version,
                        final, computed_at)
                    VALUES (:org_id, :id, :day, 9980, 10000, 20, 0, 0, 0, 0, 0, 1, false, now())
                    """
                ),
                {"org_id": project.org_id, "id": target, "day": today - timedelta(days=offset)},
            )
    await job.judge_slos(engine, slo_store, project.project_id, burning)
    rows = await _incidents(engine, project)
    assert [row.is_event for row in rows] == [False]


# ------------------------------------------------------------ exclusions (T4.7)


async def _exclude(
    engine: AsyncEngine,
    tenant: AlertTenant,
    target: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
) -> uuid.UUID:
    async with engine.begin() as connection:
        made = await connection.scalar(
            text(
                """
                INSERT INTO slo_exclusions (org_id, project_id, slo_id, starts_at, ends_at,
                                            reason)
                VALUES (:org_id, :project_id, :slo_id, :starts_at, :ends_at,
                        'Planned database upgrade')
                RETURNING id
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "slo_id": target,
                "starts_at": starts_at,
                "ends_at": ends_at,
            },
        )
    assert isinstance(made, uuid.UUID)
    return made


async def _day(engine: AsyncEngine, target: uuid.UUID, day: Any) -> Any:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text(
                    "SELECT good, total, failures, excluded_minutes, changed_why "
                    "FROM slo_daily_counts WHERE slo_id = :id AND day = :day"
                ),
                {"id": target, "day": day},
            )
        ).one()


async def test_ac60_failures_inside_an_exclusion_count_nowhere_fire_nothing_and_come_back(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    now = _now()
    burning = now - timedelta(minutes=30)
    _traffic(
        administrator,
        project,
        first=now - timedelta(hours=25),
        count=25 * 60,
        failing=_fast_burn(burning),
    )
    await _burn_rule(engine, project)
    target = await _counted_target(engine, project)
    exclusion = await _exclude(
        engine, project, target, burning - 10 * MINUTE, burning + 10 * MINUTE
    )

    await job.judge_slos(engine, slo_store, project.project_id, burning)
    assert [row for row in await _incidents(engine, project) if not row.is_event] == []
    await job.count_slos(engine, slo_store, project.project_id, now=now)
    today = burning.astimezone(UTC_ZONE).date()
    counted = await _day(engine, target, today)
    assert counted.excluded_minutes == 20
    assert counted.failures == 0

    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE slo_exclusions SET removed_at = now(), counted_at = NULL WHERE id = :id"),
            {"id": exclusion},
        )
    await job.count_slos(engine, slo_store, project.project_id, now=now + MINUTE)
    restored = await _day(engine, target, today)
    assert (restored.excluded_minutes, restored.failures) == (0, 5 * (BUSY // 2))


async def test_ac102_a_future_exclusion_changes_nothing_until_it_starts(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    now = _now()
    _traffic(administrator, project, first=now - timedelta(hours=3), count=3 * 60)
    target = await _counted_target(engine, project)
    await job.count_slos(engine, slo_store, project.project_id, now=now)
    before = await _day(engine, target, now.astimezone(UTC_ZONE).date())
    await _exclude(engine, project, target, now + timedelta(hours=2), now + timedelta(hours=3))
    await job.count_slos(engine, slo_store, project.project_id, now=now + MINUTE)
    after = await _day(engine, target, now.astimezone(UTC_ZONE).date())
    assert (after.good, after.total, after.excluded_minutes) == (
        before.good,
        before.total,
        0,
    )


async def test_ac60_each_exclusion_sends_one_notice_under_a_muted_rule_with_no_reason(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    now = _now()
    _traffic(administrator, project, first=now - timedelta(hours=2), count=120)
    async with engine.begin() as connection:
        await add_rule(
            connection,
            project.org_id,
            project.project_id,
            name="Muted targets",
            condition={"kind": "slo_burn"},
            channel_ids=[project.owners_channel_id],
            muted=True,
        )
    target = await _counted_target(engine, project)
    exclusion = await _exclude(engine, project, target, now - timedelta(hours=1), now)

    await job.judge_slos(engine, slo_store, project.project_id, now - MINUTE)
    await job.judge_slos(engine, slo_store, project.project_id, now)
    [notice] = [row for row in await _incidents(engine, project) if row.is_event]
    assert notice.subject_key == f"slo:{target}:excluded:{exclusion}"
    assert notice.rule_name == "Muted targets"
    assert "Planned database upgrade" not in str(notice.detail)
    assert await _notifications(engine, project) == [("opened", "warning")]


async def test_ac60_with_no_rule_an_exclusion_notice_goes_to_the_owners(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    now = _now()
    _traffic(administrator, project, first=now - timedelta(hours=2), count=120)
    target = await _counted_target(engine, project)
    await _exclude(engine, project, target, now - timedelta(hours=1), now)
    await job.judge_slos(engine, slo_store, project.project_id, now)
    [notice] = await _incidents(engine, project)
    assert notice.rule_name == slo.EXCLUSION_NOTICE_RULE_NAME
    async with engine.connect() as connection:
        rule_id = await connection.scalar(
            text("SELECT rule_id FROM incidents WHERE id = :id"), {"id": notice.id}
        )
        channel = await connection.scalar(
            text("SELECT channel_id FROM notifications WHERE incident_id = :id"),
            {"id": notice.id},
        )
    assert (rule_id, channel) == (None, project.owners_channel_id)


async def test_ac60_an_exclusion_overlapping_an_open_burn_shows_on_its_incident(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    burning = _now() - timedelta(hours=2)
    _traffic(
        administrator,
        project,
        first=burning - timedelta(hours=25),
        count=26 * 60,
        failing=_fast_burn(burning),
    )
    await _burn_rule(engine, project)
    target = await _counted_target(engine, project)
    await job.judge_slos(engine, slo_store, project.project_id, burning)
    exclusion = await _exclude(engine, project, target, burning + MINUTE, burning + 30 * MINUTE)
    await job.judge_slos(engine, slo_store, project.project_id, burning + 2 * MINUTE)
    [burn_incident] = [row for row in await _incidents(engine, project) if not row.is_event]
    assert [each["id"] for each in burn_incident.detail["exclusions"]] == [str(exclusion)]


async def test_t5_2_a_burn_s_incident_says_which_service_and_type_it_is_about(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    """Step 5 (T5.2): the job writes what a burn is about, which links and withholding read."""
    burning = _now() - timedelta(hours=2)
    _traffic(
        administrator,
        project,
        first=burning - timedelta(hours=25),
        count=26 * 60,
        failing=_fast_burn(burning),
    )
    await _burn_rule(engine, project)
    await _counted_target(engine, project)

    await job.judge_slos(engine, slo_store, project.project_id, burning)
    async with engine.connect() as connection:
        about = (
            await connection.execute(
                text(
                    "SELECT about_key, about_signal, about_endpoint, about_check_id "
                    "FROM incidents WHERE org_id = :org_id AND condition_kind = 'slo_burn' "
                    f"AND {_ABOUT_A_TARGET}"
                ),
                {"org_id": project.org_id},
            )
        ).one()
    assert tuple(about) == (f"service:{SERVICE}", "availability", None, None)

"""The release sync against real Postgres and ClickHouse (F28 plan T1.10, T1.13).

Rows are written into `service_versions_1m` as the worker writes them, the settled
minute is set in `detection_sweep`, and `observe_project` is run as a worker would run
the job: a rolling deploy (FR19, AC58), v2.0, v2.1 and v2.0 again (AC25, the rollback's
previous version), a return and a late first count read again (AC55), bad names beside a
good one (AC56), the record outliving rows no longer read (AC24), the lock, the forward-only
mark and the first run's bound (AC72), 50,000 junk releases (AC60), two organizations with
one service and version (AC77), a writer holding the same rows, and the sweep's hand-out.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import AsyncIterator, Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from alert_fixtures import AlertTenant, drop_tenant, make_tenant
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.client import Client
from live import require_stack
from nexovue_platform import releases as platform_releases
from settled_minute import settled_minute
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.detection.projects import session_lock
from nexovue_scheduler.detection.store import client_at
from nexovue_scheduler.releases import OBSERVE_PROJECT_TASK, RELEASES_QUEUE, sync
from nexovue_scheduler.releases.store import ReleaseStore, connect_store

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
VERSIONS: Final = "service_versions_1m"
SERVICE: Final = "checkout"
MINUTE: Final = timedelta(minutes=1)
HOUR: Final = timedelta(hours=1)
# Recent, so the rollup's 35-day TTL keeps the rows; on a whole hour, two hours back.
T0: Final = (datetime.now(UTC) - 3 * HOUR).replace(minute=0, second=0, microsecond=0)


def _reachable(url: str, port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or port), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(READER_DSN, 8123) and _reachable(POSTGRES_DSN, 5432),
    suite="F28's release sync suite",
    remedy="start the dev stack with `just dev`",
)


@pytest.fixture
def release_store() -> ReleaseStore:
    return connect_store(READER_DSN)


@pytest.fixture
def administrator() -> Iterator[Client]:
    client = client_at(CLICKHOUSE_DSN)
    try:
        yield client
    finally:
        client.close()


async def _tenant(engine: AsyncEngine) -> AlertTenant:
    made = await make_tenant(engine, with_check=False)
    delete_after_run(VERSIONS, str(made.org_id))
    return made


@pytest.fixture
async def acme(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    made = await _tenant(engine)
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


@pytest.fixture
async def globex(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    made = await _tenant(engine)
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


@pytest.fixture
async def settled(engine: AsyncEngine) -> AsyncIterator[Any]:
    """Sets the install's settled minute, held from the other suites that set it, and puts
    the one found back afterwards."""
    async with settled_minute(engine) as settle:
        yield settle


def _quoted(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace("\x00", "\\0")
    return f"'{escaped}'"


def write_versions(
    client: Client,
    tenant: AlertTenant,
    rows: Iterable[tuple[str, datetime, str, int]],
) -> None:
    """`service_versions_1m` rows, (service, minute, version, requests), as the worker
    writes them: every request in one bucket, none failing."""
    values = [
        f"('{tenant.org_id}','{tenant.project_id}',{_quoted(service)},"
        f"{int(minute.timestamp())},{_quoted(version)},{requests})"
        for service, minute, version, requests in rows
    ]
    for start in range(0, len(values), 400):
        client.command(
            f"""
            INSERT INTO {VERSIONS}
                (tenant_id, project_id, service_name, minute, version, request_count,
                 error_count, latency_buckets)
            SELECT tenant_id, project_id, service_name, toDateTime(epoch), version, requests, 0,
                   arrayReduce('sumForEachState', [arrayWithConstant(64, toUInt32(0))])
            FROM values(
                'tenant_id String, project_id String, service_name String, epoch UInt32,
                 version String, requests UInt64',
                {",".join(values[start : start + 400])}
            )
            """
        )


def traffic(
    version: str, first: datetime, minutes: int, requests: int = 10, *, service: str = SERVICE
) -> list[tuple[str, datetime, str, int]]:
    return [(service, first + MINUTE * offset, version, requests) for offset in range(minutes)]


async def _releases(engine: AsyncEngine, tenant: AlertTenant) -> dict[str, Any]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                """
                SELECT id, service_name, version, first_seen_at, last_seen_at
                FROM releases WHERE org_id = :org_id AND project_id = :project_id
                """
            ),
            {"org_id": tenant.org_id, "project_id": tenant.project_id},
        )
        return {row.version: row for row in rows}


async def _periods(engine: AsyncEngine, tenant: AlertTenant, version: str) -> list[Any]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                """
                SELECT p.started_at, p.ended_at, p.previous_version, p.already_running,
                       p.is_return, p.start_basis
                FROM release_periods AS p JOIN releases AS r
                  ON r.org_id = p.org_id AND r.id = p.release_id
                WHERE p.org_id = :org_id AND r.project_id = :project_id AND r.version = :version
                ORDER BY p.started_at
                """
            ),
            {"org_id": tenant.org_id, "project_id": tenant.project_id, "version": version},
        )
        return list(rows)


async def _synced_through(engine: AsyncEngine, tenant: AlertTenant) -> datetime | None:
    async with engine.connect() as connection:
        found = await connection.scalar(
            text("SELECT synced_through FROM release_sync_state WHERE project_id = :project_id"),
            {"project_id": tenant.project_id},
        )
    return found if isinstance(found, datetime) else None


# ------------------------------------------------------------------- periods


async def test_a_rolling_deploy_becomes_two_releases_and_the_old_period_ends(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
) -> None:
    """FR19, AC58: the first version counted is already running and has no previous."""
    write_versions(
        administrator,
        acme,
        traffic("v1", T0, 40)
        + traffic("v1", T0 + 40 * MINUTE, 10, 3)
        + traffic("v2", T0 + 40 * MINUTE, 50),
    )
    await settled(T0 + 89 * MINUTE)

    observed = await sync.observe_project(engine, release_store, acme.project_id)

    assert observed.new_releases == 2
    assert observed.synced_through == T0 + 89 * MINUTE
    found = await _releases(engine, acme)
    assert set(found) == {"v1", "v2"}
    assert found["v2"].first_seen_at == T0 + 40 * MINUTE
    (v1,) = await _periods(engine, acme, "v1")
    (v2,) = await _periods(engine, acme, "v2")
    assert v1.already_running and v1.previous_version is None
    assert v1.ended_at == T0 + 50 * MINUTE
    assert (v2.started_at, v2.ended_at, v2.previous_version) == (T0 + 40 * MINUTE, None, "v1")
    assert v2.start_basis == "seen" and not v2.already_running


async def test_a_new_release_is_summarized_30_minutes_in_and_one_already_running_is_not(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
) -> None:
    """FR55, FR43: the sync adds the summary job for the period it opens, due 30 minutes
    after its start by the newest minute read, never by a clock; a run read again opens
    nothing and adds nothing."""
    write_versions(administrator, acme, traffic("v1", T0, 40) + traffic("v2", T0 + 40 * MINUTE, 20))
    await settled(T0 + 59 * MINUTE)

    await sync.observe_project(engine, release_store, acme.project_id)
    await sync.observe_project(engine, release_store, acme.project_id)

    async with engine.connect() as connection:
        queued = list(
            await connection.execute(
                text(
                    """
                    SELECT queue, kwargs, run_after - created_at AS delay
                    FROM jobs WHERE org_id = :org_id AND task = :task
                    """
                ),
                {"org_id": acme.org_id, "task": platform_releases.SUMMARIZE_PERIOD_TASK},
            )
        )
        v2_period = await connection.scalar(
            text(
                "SELECT p.id FROM release_periods AS p JOIN releases AS r "
                "ON r.org_id = p.org_id AND r.id = p.release_id "
                "WHERE p.org_id = :org_id AND r.version = 'v2'"
            ),
            {"org_id": acme.org_id},
        )
    (job,) = queued
    assert (job.queue, job.kwargs["period_id"]) == (RELEASES_QUEUE, str(v2_period))
    # Due at T0 + 70; read through T0 + 59, so the sync's moment is T0 + 60.
    assert job.delay == 10 * MINUTE


async def _reported_before_traffic(
    engine: AsyncEngine, tenant: AlertTenant, version: str, at: datetime, state: str
) -> None:
    """A release a report filed before any traffic, with that one deploy. Only a `started`
    or `finished` report opens a period (founder, 2026-11-09), so a failed one leaves none."""
    keys = {
        "org_id": tenant.org_id,
        "project_id": tenant.project_id,
        "service": SERVICE,
        "version": version,
        "at": at,
        "state": state,
    }
    async with engine.begin() as connection:
        release_id = await connection.scalar(
            text(
                """
                INSERT INTO releases (org_id, project_id, service_name, version,
                                      first_reported_at, last_reported_at, report_count,
                                      last_state, last_activity_at)
                VALUES (:org_id, :project_id, :service, :version, :at, :at, 1, :state, :at)
                RETURNING id
                """
            ),
            keys,
        )
        await connection.execute(
            text(
                """
                INSERT INTO deploy_events (org_id, project_id, version, service_name,
                                           deployed_at, source, delivery_id, metadata, state,
                                           release_id)
                VALUES (:org_id, :project_id, :version, :service, :at, 'ci', :delivery,
                        '{}', :state, :release_id)
                """
            ),
            {**keys, "release_id": release_id, "delivery": f"delivery-{version}-{state}"},
        )
        if state in platform_releases.OPENING_STATES:
            await connection.execute(
                text(
                    """
                    INSERT INTO release_periods (org_id, release_id, project_id, service_name,
                                                 started_at, start_basis, already_running,
                                                 is_return)
                    VALUES (:org_id, :release_id, :project_id, :service, :at, 'reported',
                            false, false)
                    """
                ),
                {**keys, "release_id": release_id},
            )


@pytest.mark.parametrize("state", ["failed", "rolled_back"])
async def test_a_failed_report_before_the_first_count_leaves_it_already_running(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
    state: str,
) -> None:
    """FR43 with the founder's answer of 2026-11-09: a deploy reported as failed did not
    replace what was running, so the first version counted is still already running."""
    await _reported_before_traffic(engine, acme, "v1", T0 - 30 * MINUTE, state)
    write_versions(administrator, acme, traffic("v1", T0, 20))
    await settled(T0 + 19 * MINUTE)

    await sync.observe_project(engine, release_store, acme.project_id)

    (v1,) = await _periods(engine, acme, "v1")
    assert (v1.started_at, v1.already_running) == (T0, True)


async def test_a_finished_report_before_the_first_count_is_not_already_running(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
) -> None:
    """FR42, FR43: the reported period takes the counted minute as its start."""
    await _reported_before_traffic(engine, acme, "v1", T0 - 30 * MINUTE, "finished")
    write_versions(administrator, acme, traffic("v1", T0, 20))
    await settled(T0 + 19 * MINUTE)

    await sync.observe_project(engine, release_store, acme.project_id)

    (v1,) = await _periods(engine, acme, "v1")
    assert (v1.started_at, v1.start_basis, v1.already_running) == (T0, "seen", False)


async def test_v2_0_v2_1_v2_0_is_one_release_with_two_periods_across_runs(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
) -> None:
    """AC25: the first period keeps its start and gap; the return compares against v2.1."""
    write_versions(
        administrator, acme, traffic("v2.0", T0, 30) + traffic("v2.1", T0 + 30 * MINUTE, 30)
    )
    await settled(T0 + 59 * MINUTE)
    await sync.observe_project(engine, release_store, acme.project_id)
    first_id = (await _releases(engine, acme))["v2.0"].id

    write_versions(administrator, acme, traffic("v2.0", T0 + 60 * MINUTE, 30))
    await settled(T0 + 89 * MINUTE)
    await sync.observe_project(engine, release_store, acme.project_id)

    found = await _releases(engine, acme)
    assert found["v2.0"].id == first_id
    first, back = await _periods(engine, acme, "v2.0")
    assert (first.started_at, first.ended_at) == (T0, T0 + 30 * MINUTE)
    assert back.is_return and back.started_at == T0 + 60 * MINUTE
    assert back.previous_version == "v2.1"
    (v21,) = await _periods(engine, acme, "v2.1")
    assert v21.ended_at == T0 + 60 * MINUTE


async def test_a_version_first_counted_in_late_rows_gets_its_true_first_minute(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
) -> None:
    """AC55: a partition lagging behind the last run, within what is read again."""
    write_versions(administrator, acme, traffic("v1", T0, 60))
    await settled(T0 + 59 * MINUTE)
    await sync.observe_project(engine, release_store, acme.project_id)

    write_versions(
        administrator,
        acme,
        [(SERVICE, T0 + 57 * MINUTE, "v3", 4), *traffic("v1", T0 + 60 * MINUTE, 5)],
    )
    await settled(T0 + 64 * MINUTE)
    await sync.observe_project(engine, release_store, acme.project_id)

    assert (await _releases(engine, acme))["v3"].first_seen_at == T0 + 57 * MINUTE
    (v3,) = await _periods(engine, acme, "v3")
    assert v3.started_at == T0 + 57 * MINUTE


async def test_rows_postgres_cannot_hold_are_skipped_and_the_good_one_lands(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
) -> None:
    """AC56: an over-long and a NUL service name beside a good one."""
    write_versions(
        administrator,
        acme,
        [
            ("x" * 300, T0, "v1", 3),
            ("check\x00out", T0, "v1", 3),
            (SERVICE, T0, "v1", 3),
        ],
    )
    await settled(T0 + 5 * MINUTE)

    observed = await sync.observe_project(engine, release_store, acme.project_id)

    assert observed.rows_skipped == 2
    assert set(await _releases(engine, acme)) == {"v1"}
    assert await _synced_through(engine, acme) == T0 + 5 * MINUTE


async def test_the_record_keeps_its_id_and_first_seen_once_its_rows_are_not_read(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
) -> None:
    """AC24: a later run reads none of the first rows, as once they expire; nothing moves."""
    write_versions(administrator, acme, traffic("v1", T0, 10))
    await settled(T0 + 9 * MINUTE)
    await sync.observe_project(engine, release_store, acme.project_id)
    before = (await _releases(engine, acme))["v1"]

    write_versions(administrator, acme, traffic("v1", T0 + 150 * MINUTE, 10))
    await settled(T0 + 159 * MINUTE)
    await sync.observe_project(engine, release_store, acme.project_id)

    after = (await _releases(engine, acme))["v1"]
    assert (after.id, after.first_seen_at) == (before.id, T0)
    assert after.last_seen_at == T0 + 159 * MINUTE


# ------------------------------------------------------------------ AC72


async def test_a_second_job_for_one_project_does_nothing_while_the_first_holds_it(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
) -> None:
    write_versions(administrator, acme, traffic("v1", T0, 10))
    await settled(T0 + 9 * MINUTE)
    async with (
        engine.connect() as holder,
        session_lock(holder, "release-sync", acme.project_id) as held,
    ):
        assert held
        observed = await sync.observe_project(engine, release_store, acme.project_id)
    assert observed == sync.NOTHING_OBSERVED
    assert await _releases(engine, acme) == {}


async def test_synced_through_only_moves_forward(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
) -> None:
    write_versions(administrator, acme, traffic("v1", T0, 30))
    await settled(T0 + 29 * MINUTE)
    await sync.observe_project(engine, release_store, acme.project_id)

    await settled(T0 + 10 * MINUTE)
    await sync.observe_project(engine, release_store, acme.project_id)

    assert await _synced_through(engine, acme) == T0 + 29 * MINUTE


async def test_a_first_run_reads_no_more_than_six_hours(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
) -> None:
    write_versions(
        administrator,
        acme,
        [(SERVICE, T0 - 5 * HOUR, "too-old", 50), *traffic("v1", T0 + 2 * HOUR, 5)],
    )
    await settled(T0 + 2 * HOUR + 4 * MINUTE)

    await sync.observe_project(engine, release_store, acme.project_id)

    assert set(await _releases(engine, acme)) == {"v1"}


# ------------------------------------------------------------- caps, tenancy


async def test_50000_junk_releases_do_not_stop_a_new_real_version(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
) -> None:
    """AC60, FR56: a never-seen release of a service never heard from goes first."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO releases (org_id, project_id, service_name, version,
                                      first_reported_at, report_count, last_activity_at)
                SELECT :org_id, :project_id, 'junk-' || n, 'v' || n, :at, 1, :at
                FROM generate_series(1, :count) AS n
                """
            ),
            {
                "org_id": acme.org_id,
                "project_id": acme.project_id,
                "at": T0 - HOUR,
                "count": sync.RELEASES_PER_PROJECT,
            },
        )
    write_versions(administrator, acme, traffic("real-1", T0, 5))
    await settled(T0 + 4 * MINUTE)

    observed = await sync.observe_project(engine, release_store, acme.project_id)

    assert observed.evicted == 1
    found = await _releases(engine, acme)
    assert "real-1" in found
    assert len(found) == sync.RELEASES_PER_PROJECT


async def test_two_organizations_with_one_service_and_version_keep_their_own_rows(
    engine: AsyncEngine,
    acme: AlertTenant,
    globex: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
) -> None:
    """AC77: a sync of one never reads or writes the other's."""
    write_versions(administrator, acme, traffic("v1", T0, 20))
    write_versions(administrator, globex, traffic("v1", T0 + 10 * MINUTE, 20))
    await settled(T0 + 29 * MINUTE)

    await sync.observe_project(engine, release_store, acme.project_id)

    assert (await _releases(engine, acme))["v1"].first_seen_at == T0
    assert await _releases(engine, globex) == {}
    await sync.observe_project(engine, release_store, globex.project_id)
    ours, theirs = (await _releases(engine, acme))["v1"], (await _releases(engine, globex))["v1"]
    assert ours.id != theirs.id
    assert (ours.first_seen_at, theirs.first_seen_at) == (T0, T0 + 10 * MINUTE)


async def test_a_writer_holding_the_same_releases_does_not_break_the_sync(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: Any,
) -> None:
    """Both take row locks in (service, version) order, as the webhook will (AC51's
    shape): the sync waits, then writes, with no error."""
    write_versions(administrator, acme, traffic("a", T0, 5) + traffic("b", T0, 5))
    await settled(T0 + 4 * MINUTE)
    await sync.observe_project(engine, release_store, acme.project_id)
    write_versions(
        administrator, acme, traffic("a", T0 + 5 * MINUTE, 5) + traffic("b", T0 + 5 * MINUTE, 5)
    )
    await settled(T0 + 9 * MINUTE)

    async with engine.begin() as holder:
        await holder.execute(
            text(
                "SELECT id FROM releases WHERE org_id = :org_id AND project_id = :project_id "
                "ORDER BY service_name, version FOR UPDATE"
            ),
            {"org_id": acme.org_id, "project_id": acme.project_id},
        )
        running = asyncio.create_task(sync.observe_project(engine, release_store, acme.project_id))
        await asyncio.sleep(0.5)
        assert not running.done()
    observed = await running
    assert observed.synced_through == T0 + 9 * MINUTE


# --------------------------------------------------------------- the sweep


async def test_the_sweep_hands_a_job_to_a_moved_project_and_none_to_an_unmoved_one(
    engine: AsyncEngine, acme: AlertTenant, globex: AlertTenant
) -> None:
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        for tenant in (acme, globex):
            await connection.execute(
                text(
                    "INSERT INTO service_last_seen "
                    "(org_id, project_id, service_name, last_seen_at) "
                    "VALUES (:org_id, :project_id, :service, :at)"
                ),
                {
                    "org_id": tenant.org_id,
                    "project_id": tenant.project_id,
                    "service": SERVICE,
                    "at": now,
                },
            )
        await connection.execute(
            text(
                "INSERT INTO release_sync_state (org_id, project_id, synced_through) "
                "VALUES (:org_id, :project_id, :through)"
            ),
            {"org_id": globex.org_id, "project_id": globex.project_id, "through": now + MINUTE},
        )
    try:
        await sync.queue_observations(engine, now=now)
        async with engine.connect() as connection:
            queued = {
                (row.org_id, row.queue, row.task, row.kwargs["project_id"])
                for row in await connection.execute(
                    text("SELECT org_id, queue, task, kwargs FROM jobs WHERE org_id = ANY(:orgs)"),
                    {"orgs": [acme.org_id, globex.org_id]},
                )
            }
        assert queued == {(acme.org_id, RELEASES_QUEUE, OBSERVE_PROJECT_TASK, str(acme.project_id))}
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM jobs WHERE org_id = ANY(:orgs)"),
                {"orgs": [acme.org_id, globex.org_id]},
            )


def test_job_arguments_are_ids_only() -> None:
    request = sync.observe_request(uuid.uuid4(), uuid.uuid4())
    assert set(request.kwargs) == {"project_id"}
    assert request.queue == RELEASES_QUEUE
    assert request.expires_seconds == sync.JOB_EXPIRES_SECONDS


# ------------------------------------------------------------- retention, AC74
#
# `releases/retention.py` through the expiry sweep. Ages are counted in whole days
# from the database's own clock, so no assertion depends on the hour a test runs in.

from nexovue_scheduler.jobs import expiry  # noqa: E402
from nexovue_scheduler.releases import retention as release_retention  # noqa: E402


async def _aged_release(
    engine: AsyncEngine, tenant: AlertTenant, version: str, *, idle_days: int
) -> uuid.UUID:
    async with engine.begin() as connection:
        found = await connection.scalar(
            text(
                """
                INSERT INTO releases (org_id, project_id, service_name, version,
                                      first_seen_at, last_activity_at)
                VALUES (:org_id, :project_id, 'checkout', :version,
                        now() - make_interval(days => :days),
                        now() - make_interval(days => :days))
                RETURNING id
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "version": version,
                "days": idle_days,
            },
        )
    assert isinstance(found, uuid.UUID)
    return found


async def _aged_deploy(
    engine: AsyncEngine,
    tenant: AlertTenant,
    *,
    arrived_days: int,
    claimed_days: int,
    release_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with engine.begin() as connection:
        found = await connection.scalar(
            text(
                """
                INSERT INTO deploy_events (org_id, project_id, service_name, version,
                                           deployed_at, source, delivery_id, release_id,
                                           created_at)
                VALUES (:org_id, :project_id, 'checkout', 'v1',
                        now() - make_interval(days => :claimed), 'ci', :delivery,
                        :release_id, now() - make_interval(days => :arrived))
                RETURNING id
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "claimed": claimed_days,
                "arrived": arrived_days,
                "delivery": f"retention-{uuid.uuid4()}",
                "release_id": release_id,
            },
        )
    assert isinstance(found, uuid.UUID)
    return found


async def _left(engine: AsyncEngine, table: str, ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(f"SELECT id FROM {table} WHERE id = ANY(:ids)"), {"ids": list(ids)}
        )
    return {row[0] for row in rows}


async def test_a_30_day_plan_keeps_29_days_and_drops_31_by_arrival_time(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC74: by our clock, so a sender's back-dated `deployed_at` shortens nothing; a
    release lapsing while its deploy lives does not stop the sweep."""
    old = await _aged_release(engine, acme, "v-old", idle_days=31)
    young = await _aged_release(engine, acme, "v-young", idle_days=29)
    gone = await _aged_deploy(engine, acme, arrived_days=31, claimed_days=31)
    back_dated = await _aged_deploy(engine, acme, arrived_days=1, claimed_days=40)
    outliving = await _aged_deploy(engine, acme, arrived_days=29, claimed_days=29, release_id=old)

    await expiry.sweep(engine)

    assert await _left(engine, "releases", (old, young)) == {young}
    assert await _left(engine, "deploy_events", (gone, back_dated, outliving)) == {
        back_dated,
        outliving,
    }
    async with engine.connect() as connection:
        kept_release = await connection.scalar(
            text("SELECT release_id FROM deploy_events WHERE id = :id"), {"id": outliving}
        )
    assert kept_release is None


async def test_a_395_day_plan_keeps_394_days_and_drops_396(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC74, on a paid plan."""
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE organizations SET plan = 'pro' WHERE id = :id"), {"id": acme.org_id}
        )
    old = await _aged_release(engine, acme, "v-old", idle_days=396)
    young = await _aged_release(engine, acme, "v-young", idle_days=394)
    gone = await _aged_deploy(engine, acme, arrived_days=396, claimed_days=396)
    kept = await _aged_deploy(engine, acme, arrived_days=394, claimed_days=394)

    await expiry.sweep(engine)

    assert await _left(engine, "releases", (old, young)) == {young}
    assert await _left(engine, "deploy_events", (gone, kept)) == {kept}


async def test_a_long_deleted_project_s_rows_lapse_and_a_newly_deleted_one_s_wait(
    engine: AsyncEngine, acme: AlertTenant, globex: AlertTenant
) -> None:
    """FR40, AC74: past the shortest plan's retention after deletion, whatever their
    activity."""
    long_gone = await _aged_release(engine, acme, "v1", idle_days=1)
    long_gone_deploy = await _aged_deploy(engine, acme, arrived_days=1, claimed_days=1)
    recent = await _aged_release(engine, globex, "v1", idle_days=1)
    async with engine.begin() as connection:
        for tenant, days in ((acme, 31), (globex, 1)):
            await connection.execute(
                text(
                    "UPDATE projects SET deleted_at = now() - make_interval(days => :days) "
                    "WHERE org_id = :org_id AND id = :id"
                ),
                {"days": days, "org_id": tenant.org_id, "id": tenant.project_id},
            )

    await expiry.sweep(engine)

    assert await _left(engine, "releases", (long_gone, recent)) == {recent}
    assert await _left(engine, "deploy_events", (long_gone_deploy,)) == set()


async def test_one_table_failing_does_not_stop_the_tables_after_it(
    engine: AsyncEngine, acme: AlertTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design 3, *Retention*: the sweep had no per-table guard."""
    old = await _aged_release(engine, acme, "v-old", idle_days=31)
    monkeypatch.setattr(
        expiry,
        "_EXPIRED",
        (("no_such_table", "id", "true"), *release_retention.EXPIRED),
    )

    removed = await expiry.sweep(engine)

    assert removed["no_such_table"] == 0
    assert await _left(engine, "releases", (old,)) == set()

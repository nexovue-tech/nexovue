"""The stored reading and the release summary against real Postgres and ClickHouse (F28
plan T5.2, T5.6; AC77, AC81, AC82).

Version rows are written into `service_versions_1m` and `service_stats_1m` as the worker
writes them, the settled minute is set, and the summary job is run as a worker runs it:
the comparison read through the scheduler's own store by the API's rule, stored with no
names (AC81); with the opt-in off, nothing written; with it on, one notice per release
on the chosen channel whatever its reading, a retried job writing nothing twice, two
releases in a row both told, and none for a version already running (AC82); the
database refusing a summary that names no period; deleting the channel turning it off;
a PagerDuty channel never written to; and delivery putting the counts into words, with
a hostile version flattened, and no finding opened.

The rows are recent, since the rollup's TTL keeps 35 days, and every decision takes a
`now` built from them, never the wall clock's minute. Each test's tenants and rows are
its own, removed afterwards. Fails rather than skips when a store is down.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from alert_fixtures import AlertTenant, add_channel, drop_tenant, make_tenant, rows
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.client import Client
from hostile_release_names import HOSTILE_VERSIONS
from live import require_stack
from nexovue_platform import releases
from nexovue_platform.email import Delivery, Message
from nexovue_platform.slo import BUCKET_COUNT
from settled_minute import settled_minute
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import delivery
from nexovue_scheduler.alerts.delivery import deliver_due
from nexovue_scheduler.alerts.dispatch import Outbound
from nexovue_scheduler.detection.store import client_at
from nexovue_scheduler.releases import facts, summary
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
SERVICE: Final = "checkout"
MINUTE: Final = timedelta(minutes=1)
HOUR: Final = timedelta(hours=1)
# Recent, so the rollup's TTL keeps the rows, on a whole hour. Every decision below is
# taken at a `now` built from it.
T0: Final = (datetime.now(UTC) - 3 * HOUR).replace(minute=0, second=0, microsecond=0)
# Thirty minutes in, with the settled minute just before: the job's own moment.
DUE: Final = T0 + releases.SUMMARY_AFTER
SETTLED: Final = DUE - MINUTE
FAST: Final = 30


def _reachable(url: str, port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or port), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(READER_DSN, 8123) and _reachable(POSTGRES_DSN, 5432),
    suite="F28's release summary suite",
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
    for table in ("service_versions_1m", "service_stats_1m"):
        delete_after_run(table, str(made.org_id))
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
async def settled(engine: AsyncEngine) -> AsyncIterator[None]:
    """The install's settled minute at `SETTLED`, held from the other suites that set it."""
    async with settled_minute(engine) as settle:
        await settle(SETTLED)
        yield


def _buckets(requests: int) -> list[int]:
    counts = [0] * BUCKET_COUNT
    counts[FAST] = requests
    return counts


def _quoted(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def write_minutes(
    client: Client,
    tenant: AlertTenant,
    minutes: Iterable[tuple[datetime, str, int, int]],
    *,
    service: str = SERVICE,
) -> None:
    """(minute, version, requests, errors) into the version rollup, and each minute's sum
    into `service_stats_1m`, as the worker writes both."""
    totals: dict[datetime, list[int]] = {}
    versions = []
    for minute, version, requests, errors in minutes:
        versions.append(
            f"('{tenant.org_id}','{tenant.project_id}','{service}',{int(minute.timestamp())},"
            f"{_quoted(version)},{requests},{errors},{_buckets(requests)})"
        )
        total = totals.setdefault(minute, [0, 0])
        total[0] += requests
        total[1] += errors
    stats = [
        f"('{tenant.org_id}','{tenant.project_id}','{service}',{int(minute.timestamp())},"
        f"{requests},{errors},{_buckets(requests)})"
        for minute, (requests, errors) in totals.items()
    ]
    client.command(
        f"""
        INSERT INTO service_versions_1m
            (tenant_id, project_id, service_name, minute, version, request_count, error_count,
             latency_buckets)
        SELECT tenant_id, project_id, service_name, toDateTime(epoch), version, requests,
               errors, arrayReduce('sumForEachState', [buckets])
        FROM values('tenant_id String, project_id String, service_name String, epoch UInt32,
                     version String, requests UInt64, errors UInt64, buckets Array(UInt32)',
                    {",".join(versions)})
        """
    )
    client.command(
        f"""
        INSERT INTO service_stats_1m
            (tenant_id, project_id, service_name, minute, request_count, error_count,
             latency_buckets)
        SELECT tenant_id, project_id, service_name, toDateTime(epoch), requests, errors,
               arrayReduce('sumForEachState', [buckets])
        FROM values('tenant_id String, project_id String, service_name String, epoch UInt32,
                     requests UInt64, errors UInt64, buckets Array(UInt32)',
                    {",".join(stats)})
        """
    )


def rolling(version: str, previous: str, *, errors: int = 6) -> list[Any]:
    """An hour of the previous version, then 30 minutes of both side by side, the new
    one failing `errors` in 200 a minute against none."""
    before = [(T0 - offset * MINUTE, previous, 200, 0) for offset in range(1, 61)]
    both = [
        (T0 + offset * MINUTE, name, 200, failed)
        for offset in range(30)
        for name, failed in ((version, errors), (previous, 0))
    ]
    return before + both


async def _period(
    engine: AsyncEngine,
    tenant: AlertTenant,
    version: str,
    previous: str | None,
    *,
    service: str = SERVICE,
    started_at: datetime = T0,
    already_running: bool = False,
) -> tuple[uuid.UUID, uuid.UUID]:
    """A seen release and its one period, as the sync writes them."""
    async with engine.begin() as connection:
        release_id: uuid.UUID = await connection.scalar(
            text(
                """
                INSERT INTO releases (org_id, project_id, service_name, version, first_seen_at,
                                      last_seen_at, last_activity_at)
                VALUES (:org_id, :project_id, :service, :version, :at, :at, now())
                RETURNING id
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "service": service,
                "version": version,
                "at": started_at,
            },
        )
        period_id: uuid.UUID = await connection.scalar(
            text(
                """
                INSERT INTO release_periods (org_id, release_id, project_id, service_name,
                                             started_at, start_basis, previous_version,
                                             already_running, is_return)
                VALUES (:org_id, :release_id, :project_id, :service, :at, 'seen', :previous,
                        :running, false)
                RETURNING id
                """
            ),
            {
                "org_id": tenant.org_id,
                "release_id": release_id,
                "project_id": tenant.project_id,
                "service": service,
                "at": started_at,
                "previous": previous,
                "running": already_running,
            },
        )
    return release_id, period_id


async def _summarize(
    engine: AsyncEngine,
    store: ReleaseStore,
    tenant: AlertTenant,
    period_id: uuid.UUID,
    *,
    now: datetime = DUE,
) -> summary.Summarized:
    return await summary.summarize_period(
        engine,
        store,
        org_id=tenant.org_id,
        project_id=tenant.project_id,
        period_id=period_id,
        now=now,
    )


async def _opt_in(engine: AsyncEngine, tenant: AlertTenant, *, kind: str = "slack") -> uuid.UUID:
    async with engine.begin() as connection:
        channel_id = (
            tenant.owners_channel_id
            if kind == "owners"
            else await add_channel(connection, tenant.org_id, kind=kind, name=f"Releases {kind}")
        )
        await connection.execute(
            text(
                "UPDATE projects SET release_summary_channel_id = :channel "
                "WHERE org_id = :org_id AND id = :project_id"
            ),
            {"channel": channel_id, "org_id": tenant.org_id, "project_id": tenant.project_id},
        )
    return channel_id


async def _stored(engine: AsyncEngine, period_id: uuid.UUID) -> Any:
    (row,) = await rows(
        engine,
        "SELECT last_reading, mixed_environments FROM release_periods WHERE id = :id",
        id=period_id,
    )
    return row


async def _summaries(engine: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    return await rows(
        engine,
        """
        SELECT release_period_id, channel_id, kind, sequence, severity, state
        FROM notifications WHERE org_id = :org_id ORDER BY created_at, id
        """,
        org_id=tenant.org_id,
    )


# --------------------------------------------------- T5.2: the comparison, AC77


async def test_the_scheduler_compares_by_the_api_s_rule_from_its_own_store(
    engine: AsyncEngine,
    acme: AlertTenant,
    globex: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: None,
) -> None:
    """T5.2, AC77: side by side with exact counts, from this organization's rows alone,
    though another holds the same service and version with ten times the traffic."""
    write_minutes(administrator, acme, rolling("v2", "v1"))
    write_minutes(
        administrator,
        globex,
        [
            (minute, version, requests * 10, 0)
            for minute, version, requests, _ in rolling("v2", "v1")
        ],
    )
    _, period_id = await _period(engine, acme, "v2", "v1")
    await _period(engine, globex, "v2", "v1")

    async with engine.connect() as connection:
        found = await facts.release_comparison(
            release_store,
            connection,
            org_id=acme.org_id,
            project_id=acme.project_id,
            period_id=period_id,
            window=releases.DEFAULT_WINDOW,
            now=DUE,
        )

    assert found is not None
    (block,) = found.blocks
    assert block.method == releases.SIDE_BY_SIDE
    assert (block.this_version.requests, block.this_version.errors) == (6000, 180)
    assert (block.previous_version.requests, block.previous_version.errors) == (6000, 0)
    assert block.failure_share.reading == releases.HIGHER
    assert (found.compared_to, found.minutes_differing, found.versions_combined) == (DUE, 0, False)
    assert found.mixed_environments is False


# ------------------------------------------------------------ AC81: the reading


async def test_with_the_opt_in_off_the_reading_is_stored_and_nothing_is_sent(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: None,
) -> None:
    """AC81, AC82: the reading 30 minutes in, holding no names; off by default, no notice."""
    write_minutes(administrator, acme, rolling("v2", "v1"))
    _, period_id = await _period(engine, acme, "v2", "v1")

    done = await _summarize(engine, release_store, acme, period_id)

    stored = await _stored(engine, period_id)
    assert (done.stored, done.notified) == (True, False)
    assert stored.last_reading["method"] == releases.SIDE_BY_SIDE
    assert stored.last_reading["failure_share"] == releases.HIGHER
    assert (
        stored.last_reading["this"]["requests"],
        stored.last_reading["this"]["errors"],
        stored.last_reading["previous"]["requests"],
    ) == (6000, 180, 6000)
    assert "v1" not in json.dumps(stored.last_reading)
    assert "v2" not in json.dumps(stored.last_reading)
    assert stored.mixed_environments is False
    assert await _summaries(engine, acme) == []


async def test_a_summary_before_its_30_minutes_puts_itself_back(
    engine: AsyncEngine, acme: AlertTenant, release_store: ReleaseStore, settled: None
) -> None:
    """A start that moved later, as a reported one does once counted, is read when due."""
    _, period_id = await _period(engine, acme, "v2", "v1")

    done = await _summarize(engine, release_store, acme, period_id, now=T0 + 10 * MINUTE)

    assert (done.stored, done.again_in) == (False, 20 * 60)
    assert (await _stored(engine, period_id)).last_reading is None
    queued = await rows(
        engine,
        "SELECT task, queue, kwargs FROM jobs WHERE org_id = :org_id",
        org_id=acme.org_id,
    )
    assert [(job.task, job.queue, job.kwargs["period_id"]) for job in queued] == [
        (releases.SUMMARIZE_PERIOD_TASK, releases.RELEASES_QUEUE, str(period_id))
    ]


# -------------------------------------------------------------- AC82: the notice


async def test_each_release_is_told_once_on_its_channel_whatever_its_reading(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: None,
) -> None:
    """AC82: two releases in a row on one channel, one with numbers and one with only a
    reason, are each told once; the job run again writes nothing twice; nothing pages and
    no finding opens."""
    channel_id = await _opt_in(engine, acme)
    write_minutes(administrator, acme, rolling("v2", "v1"))
    write_minutes(
        administrator,
        acme,
        [(T0 + offset * MINUTE, "s1", 50, 0) for offset in range(30)],
        service="search",
    )
    _, compared = await _period(engine, acme, "v2", "v1")
    _, first = await _period(engine, acme, "s1", None, service="search")

    told = [
        await _summarize(engine, release_store, acme, compared),
        await _summarize(engine, release_store, acme, first),
        await _summarize(engine, release_store, acme, compared),
    ]

    assert [done.notified for done in told] == [True, True, False]
    summaries = await _summaries(engine, acme)
    assert [(row.release_period_id, row.channel_id) for row in summaries] == [
        (compared, channel_id),
        (first, channel_id),
    ]
    assert {(row.kind, row.sequence, row.severity, row.state) for row in summaries} == {
        ("release_summary", 0, "warning", "pending")
    }
    assert (await _stored(engine, first)).last_reading["reason"] == releases.NO_PREVIOUS_VERSION
    assert await rows(engine, "SELECT id FROM findings WHERE org_id = :o", o=acme.org_id) == []


async def test_a_version_already_running_is_not_a_release_and_is_not_told(
    engine: AsyncEngine, acme: AlertTenant, release_store: ReleaseStore, settled: None
) -> None:
    """FR43, AC82: neither a reading nor a notice."""
    await _opt_in(engine, acme)
    _, period_id = await _period(engine, acme, "v1", None, already_running=True)

    done = await _summarize(engine, release_store, acme, period_id)

    assert (done.stored, done.notified) == (False, False)
    assert (await _stored(engine, period_id)).last_reading is None
    assert await _summaries(engine, acme) == []


async def test_a_pagerduty_channel_is_never_written_to_whatever_the_column_holds(
    engine: AsyncEngine, acme: AlertTenant, release_store: ReleaseStore, settled: None
) -> None:
    """A summary never pages (D52): the writer joins only channels people read."""
    await _opt_in(engine, acme, kind="pagerduty")
    _, period_id = await _period(engine, acme, "v2", "v1")

    done = await _summarize(engine, release_store, acme, period_id)

    assert (done.stored, done.notified) == (True, False)
    assert await _summaries(engine, acme) == []


async def _notice(engine: AsyncEngine, tenant: AlertTenant, kind: str, period: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO notifications (org_id, project_id, release_period_id, channel_id,
                                           kind, severity)
                VALUES (:org_id, :project_id, :period, :channel, :kind, 'warning')
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "period": period,
                "channel": tenant.owners_channel_id,
                "kind": kind,
            },
        )


async def test_the_database_refuses_a_summary_with_no_period_and_a_period_on_another_kind(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC82: a summary names its period, and only a summary names one
    (`names_one_subject`, `release_summary_names_its_period`)."""
    _, period_id = await _period(engine, acme, "v2", "v1")

    for kind, period in (("release_summary", None), ("opened", period_id)):
        with pytest.raises(IntegrityError):
            await _notice(engine, acme, kind, period)
    await _notice(engine, acme, "release_summary", period_id)

    assert [(row.kind, row.release_period_id) for row in await _summaries(engine, acme)] == [
        ("release_summary", period_id)
    ]


async def test_deleting_the_channel_turns_the_summary_off(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC82, D53: `ON DELETE SET NULL`."""
    channel_id = await _opt_in(engine, acme)
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM notification_channels WHERE org_id = :o AND id = :id"),
            {"o": acme.org_id, "id": channel_id},
        )

    (project,) = await rows(
        engine,
        "SELECT release_summary_channel_id FROM projects WHERE id = :id",
        id=acme.project_id,
    )
    assert project.release_summary_channel_id is None


# --------------------------------------------------------- AC82: what is sent


class _Mailbox:
    def __init__(self) -> None:
        self.bodies: list[str] = []
        self.subjects: list[str] = []
        self.links: list[str] = []

    async def send(
        self,
        *,
        to: str,
        message: Message,
        data: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> Delivery:
        self.bodies.append(str(data["body"]))
        self.subjects.append(str(data["subject"]))
        self.links.append(str(data["link"]))
        return Delivery(provider_message_id="recorded")


async def _deliver(engine: AsyncEngine, tenant: AlertTenant, at: datetime) -> _Mailbox:
    mailbox = _Mailbox()
    await deliver_due(
        engine,
        Outbound(email_sender=mailbox, secret_box=None, app_base_url="https://app.test"),
        now=lambda: at,
        org_id=tenant.org_id,
    )
    return mailbox


async def test_a_summary_is_delivered_with_its_counts_and_a_hostile_version_as_words(
    engine: AsyncEngine,
    acme: AlertTenant,
    administrator: Client,
    release_store: ReleaseStore,
    settled: None,
) -> None:
    """AC82: counts and plain words, "Order only, not cause", the version read at send
    as one line; then two releases in a row go as one message to people."""
    hostile = HOSTILE_VERSIONS[0]
    await _opt_in(engine, acme, kind="owners")
    write_minutes(administrator, acme, rolling(hostile, "v1"))
    release_id, period_id = await _period(engine, acme, hostile, "v1")
    await _summarize(engine, release_store, acme, period_id)

    mailbox = await _deliver(engine, acme, DUE)

    (body,) = mailbox.bodies
    assert mailbox.subjects == [f"Release summary: {hostile} of checkout"]
    assert (
        f"Failure share: 3.0% (180 of 6,000) on {hostile} against 0.0% (0 of 6,000) on v1, "
        "higher." in body
    )
    assert "Order only, not cause." in body
    assert mailbox.links == [f"https://app.test/releases/{release_id}"]
    assert [row.state for row in await _summaries(engine, acme)] == ["delivered"]

    write_minutes(
        administrator,
        acme,
        [(T0 + offset * MINUTE, "s1", 50, 0) for offset in range(30)],
        service="search",
    )
    _, second = await _period(engine, acme, "s1", None, service="search")
    _, third = await _period(engine, acme, "s2", "s1", service="search", started_at=T0 + MINUTE)
    for each in (second, third):
        await _summarize(engine, release_store, acme, each, now=DUE + MINUTE)

    later = await _deliver(engine, acme, DUE + MINUTE)

    assert later.subjects == ["2 release summaries"]
    assert later.bodies == ["Release summary: s1 of search\nRelease summary: s2 of search"]
    assert [row.state for row in await _summaries(engine, acme)] == ["delivered"] * 3


async def test_a_release_that_cannot_be_read_is_tried_again_and_not_sent_without_words(
    engine: AsyncEngine,
    acme: AlertTenant,
    release_store: ReleaseStore,
    settled: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D52, AC42: the words are read after the claim on their own connection; a read
    that fails sends nothing and leaves the notice to be tried again."""
    await _opt_in(engine, acme, kind="owners")
    _, period_id = await _period(engine, acme, "v2", "v1")
    await _summarize(engine, release_store, acme, period_id)

    async def unreadable(*_: Any, **__: Any) -> Any:
        raise OSError("release tables unreachable")

    monkeypatch.setattr(facts, "summarized_periods", unreadable)
    mailbox = await _deliver(engine, acme, DUE)

    assert mailbox.bodies == []
    (notice,) = await rows(
        engine,
        "SELECT state, attempts, outcome_reason FROM notifications WHERE org_id = :o",
        o=acme.org_id,
    )
    assert (notice.state, notice.attempts, notice.outcome_reason) == (
        "pending",
        1,
        delivery.SUMMARY_UNREAD_REASON,
    )


async def test_a_summary_waiting_on_a_retry_never_holds_the_next_release_s(
    engine: AsyncEngine,
    acme: AlertTenant,
    release_store: ReleaseStore,
    settled: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Design section 6: each summary is its period's own subject in the outbox's order,
    so one waiting to be tried again does not hold the next release's on the channel."""
    await _opt_in(engine, acme, kind="owners")
    _, first = await _period(engine, acme, "v2", "v1")
    await _summarize(engine, release_store, acme, first)

    async def unreadable(*_: Any, **__: Any) -> Any:
        raise OSError("release tables unreachable")

    with monkeypatch.context() as patched:
        patched.setattr(facts, "summarized_periods", unreadable)
        await _deliver(engine, acme, DUE)
    _, second = await _period(engine, acme, "v3", "v2", service="search")
    await _summarize(engine, release_store, acme, second, now=DUE + timedelta(seconds=1))

    # The first waits at least 24 seconds for its retry; the second is due now.
    mailbox = await _deliver(engine, acme, DUE + timedelta(seconds=5))

    assert mailbox.subjects == ["Release summary: v3 of search"]

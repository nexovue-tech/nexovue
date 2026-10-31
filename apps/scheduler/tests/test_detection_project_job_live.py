"""One project's judging against real Postgres and ClickHouse (plan T2c.3, T3.3).

History is written straight into `service_stats_1m` with the store's own account,
the hourly baseline job builds the normal from it, and the project job is run
minute by minute, as the coordinator would hand it settled minutes. This suite
fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from alert_fixtures import AlertTenant, drop_tenant, make_tenant
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.client import Client
from live import require_stack
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_scheduler.detection import baselines, project_job
from nexovue_scheduler.detection.coordinator import project_requests
from nexovue_scheduler.detection.method import BUCKET_COUNT, RECOVERY_MINUTES
from nexovue_scheduler.detection.project_job import (
    Outcome,
    ServiceState,
    StaleJudgementError,
    judge_project,
)
from nexovue_scheduler.detection.projects import ActiveProject, active_projects
from nexovue_scheduler.detection.store import (
    READINGS_TABLE,
    DetectionStore,
    bind_project,
    client_at,
    connect,
)

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
WRITER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DETECTION_WRITER_DSN",
    "clickhouse://nexovue_detection_writer:nexovue_detection_writer_dev@localhost:18123/nexovue",
)
POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ROLLUP: Final = "service_stats_1m"
ENDPOINT_ROLLUP: Final = "endpoint_stats_1m"
SERVICE: Final = "checkout"
MINUTE: Final = timedelta(minutes=1)
# Normal sensitivity, the default: a 5-minute window, so 5 minutes of sustain.
SUSTAIN: Final = 5
# Time to a finding beyond critical, at normal sensitivity (spec, *Product values*).
TIME_TO_A_FINDING: Final = timedelta(minutes=12)
# The minute the trouble starts: noon today, far from any midnight a pool stops at.
ONSET: Final = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
# A steady latency shape over 11 buckets, in percent, at 100 requests a minute.
SHAPE: Final = (1, 3, 8, 15, 22, 20, 14, 9, 5, 2, 1)
FIRST_BUCKET: Final = 20
REQUESTS: Final = 100
# Buckets are about 1.19x wide: 5 is about 2.4x slower, 10 about 5.6x, past critical.
PAST_THE_MINIMUM: Final = 5
PAST_CRITICAL: Final = 10
_INSERT_BATCH: Final = 400


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or default_port), 2
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN, 8123) and _reachable(POSTGRES_DSN, 5432),
    suite="apps/scheduler/tests/test_detection_project_job_live.py",
    remedy="ClickHouse or Postgres is not reachable — run `just up`",
)


@pytest.fixture
def store() -> DetectionStore:
    return connect(READER_DSN, WRITER_DSN)


@pytest.fixture
def administrator() -> Iterator[Client]:
    client = client_at(CLICKHOUSE_DSN)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
async def project(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    made = await make_tenant(engine, with_check=False)
    delete_after_run(ROLLUP, str(made.org_id))
    delete_after_run(ENDPOINT_ROLLUP, str(made.org_id))
    delete_after_run(READINGS_TABLE, str(made.org_id))
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


# ------------------------------------------------------------------ seeding


def _buckets(slower_by: int, requests: int = REQUESTS) -> list[int]:
    buckets = [0] * BUCKET_COUNT
    for offset, percent in enumerate(SHAPE):
        buckets[FIRST_BUCKET + offset + slower_by] += requests * percent // 100
    buckets[FIRST_BUCKET + 4 + slower_by] += requests - sum(buckets)
    return buckets


def _seed_endpoints(
    client: Client,
    tenant: AlertTenant,
    service: str,
    endpoints: Mapping[datetime, Mapping[str, tuple[int, int]]],
) -> None:
    rows = [
        f"('{tenant.org_id}','{tenant.project_id}','{service}','SERVER','{name}',"
        f"{int(minute.timestamp())},{requests},{errors})"
        for minute, counts in endpoints.items()
        for name, (requests, errors) in counts.items()
    ]
    for start in range(0, len(rows), _INSERT_BATCH):
        client.command(
            f"""
            INSERT INTO {ENDPOINT_ROLLUP}
                (tenant_id, project_id, service_name, span_kind, span_name, minute,
                 request_count, error_count)
            SELECT tenant_id, project_id, service_name, span_kind, span_name,
                   toDateTime(epoch), requests, errors
            FROM values(
                'tenant_id String, project_id String, service_name String, span_kind String,
                 span_name String, epoch UInt32, requests UInt64, errors UInt64',
                {",".join(rows[start : start + _INSERT_BATCH])}
            )
            """
        )


def _seed(
    client: Client,
    tenant: AlertTenant,
    minutes: Sequence[datetime],
    *,
    service: str = SERVICE,
    slower_by: dict[datetime, int] | None = None,
    requests: int = REQUESTS,
    errors: int = 0,
    endpoints: Mapping[datetime, Mapping[str, tuple[int, int]]] | None = None,
) -> None:
    """Rows as the worker writes them: the buckets folded into their aggregate state,
    and each endpoint's counts in its own rollup."""
    rows = []
    for minute in minutes:
        buckets = _buckets((slower_by or {}).get(minute, 0), requests)
        failures = errors
        if endpoints is not None and minute in endpoints:
            failures = sum(e for _, e in endpoints[minute].values())
        rows.append(
            f"('{tenant.org_id}','{tenant.project_id}','{service}',"
            f"{int(minute.timestamp())},{sum(buckets)},{failures},{buckets})"
        )
    if endpoints is not None:
        _seed_endpoints(client, tenant, service, endpoints)
    for start in range(0, len(rows), _INSERT_BATCH):
        client.command(
            f"""
            INSERT INTO {ROLLUP}
                (tenant_id, project_id, service_name, minute, request_count, error_count,
                 latency_buckets)
            SELECT tenant_id, project_id, service_name, toDateTime(epoch), requests, errors,
                   arrayReduce('sumForEachState', [buckets])
            FROM values(
                'tenant_id String, project_id String, service_name String, epoch UInt32,
                 requests UInt64, errors UInt64, buckets Array(UInt32)',
                {",".join(rows[start : start + _INSERT_BATCH])}
            )
            """
        )


def _span(first: datetime, last: datetime) -> list[datetime]:
    return [first + step * MINUTE for step in range((last - first) // MINUTE + 1)]


def _seed_four_weeks(
    client: Client,
    tenant: AlertTenant,
    *,
    service: str = SERVICE,
    errors: int = 0,
    endpoints: Mapping[str, tuple[int, int]] | None = None,
    until: timedelta = timedelta(minutes=240),
) -> None:
    """A service first seen 29 days ago, with normal traffic around noon on the four
    same weekdays its weekly pool reads; and its endpoints, when given."""
    _seed(client, tenant, [ONSET - timedelta(days=29)], service=service)
    for weeks in range(1, 5):
        day = ONSET - timedelta(days=7 * weeks)
        minutes = _span(day - timedelta(minutes=200), day + until)
        _seed(
            client,
            tenant,
            minutes,
            service=service,
            errors=errors,
            endpoints=None if endpoints is None else dict.fromkeys(minutes, endpoints),
        )


async def _baselines(
    engine: AsyncEngine,
    store: DetectionStore,
    tenant: AlertTenant,
    *,
    now: datetime = ONSET - timedelta(minutes=5),
) -> None:
    written = await baselines.compute_project(engine, store, tenant.project_id, now=now)
    assert written is not None and written >= 1


async def _judge_through(
    engine: AsyncEngine,
    store: DetectionStore,
    tenant: AlertTenant,
    minutes: Sequence[datetime],
) -> list[Outcome]:
    """The coordinator's minutes, one settled minute after another."""
    return [
        await judge_project(engine, store, tenant.project_id, settled=minute, now=minute)
        for minute in minutes
    ]


async def _findings(engine: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            await connection.execute(
                text(
                    "SELECT * FROM findings WHERE org_id = :org_id AND project_id = :project_id "
                    "ORDER BY started_at"
                ),
                {"org_id": tenant.org_id, "project_id": tenant.project_id},
            )
        )


async def _state(engine: AsyncEngine, tenant: AlertTenant, service: str = SERVICE) -> Any:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text(
                    "SELECT * FROM detection_state WHERE org_id = :org_id "
                    "AND project_id = :project_id AND service_name = :service"
                ),
                {"org_id": tenant.org_id, "project_id": tenant.project_id, "service": service},
            )
        ).one()


def _rise(client: Client, tenant: AlertTenant, *, minutes: int, slower_by: int) -> list[datetime]:
    """Normal from 30 minutes before the onset, `minutes` slower from it, then
    normal for 40 minutes. The minutes to judge: 10 before the onset to 30 after."""
    span = _span(ONSET - timedelta(minutes=30), ONSET + timedelta(minutes=40))
    slow = {ONSET + step * MINUTE: slower_by for step in range(minutes)}
    _seed(client, tenant, span, slower_by=slow)
    return _span(ONSET - timedelta(minutes=10), ONSET + timedelta(minutes=30))


# ------------------------------------------------------------------- tests


async def test_a_sustained_slowdown_opens_exactly_one_finding_and_closes_it(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC8: a rise past the minimum for *sustain* opens one finding, and it closes
    as recovered after *recovery* normal minutes (FR13)."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    minutes = _rise(administrator, project, minutes=SUSTAIN, slower_by=PAST_THE_MINIMUM)

    outcomes = await _judge_through(engine, store, project, minutes)

    assert sum(outcome.opened for outcome in outcomes) == 1
    assert sum(outcome.closed for outcome in outcomes) == 1
    (finding,) = await _findings(engine, project)
    assert finding.service_name == SERVICE
    assert finding.state == "closed" and finding.closed_reason == "recovered"
    assert finding.severity == "warning"
    assert finding.started_at == ONSET
    assert finding.title.startswith(f"{SERVICE} is 2.")
    assert finding.title.endswith(f"slower than normal since {ONSET:%H:%M} UTC")
    (signal,) = finding.signals
    assert signal["signal"] == "slower"
    assert set(signal) == {"signal", "normal", "current", "worst", "comparison", "endpoint"}
    assert signal["worst"] > 2 * signal["normal"]
    weekday = f"{ONSET:%A}s"
    assert signal["comparison"].startswith(f"{weekday} around 12:0")
    assert signal["comparison"].endswith(", last 4 weeks")
    state = await _state(engine, project)
    assert state.last_judged_minute == minutes[-1]
    # A closed finding leaves no keys to count; the signal's own clear run continues.
    assert "finding" not in state.signals
    assert state.signals["slower"]["clear_minutes"] >= RECOVERY_MINUTES
    readings = store.for_project(await _bound(engine, project)).readings(
        start=minutes[0], end=minutes[-1] + MINUTE
    )
    assert [reading.minute for reading in readings] == list(minutes)
    assert {reading.slower_mode for reading in readings} == {"weekly"}
    assert "unusual" in {reading.slower_verdict for reading in readings}


async def test_a_rise_too_short_to_pass_the_80_percent_rule_opens_nothing(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC8's second half, under the 80% rule the founder kept: 3 slow minutes of a
    5-minute window, one short of the 4 that open (test_detection_method.py)."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    minutes = _rise(administrator, project, minutes=SUSTAIN - 2, slower_by=PAST_THE_MINIMUM)

    outcomes = await _judge_through(engine, store, project, minutes)

    assert sum(outcome.opened for outcome in outcomes) == 0
    assert await _findings(engine, project) == []


async def test_a_slowdown_beyond_critical_is_a_critical_finding_in_time(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC12, *slower*: within *time to a finding* of the onset, and critical."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    minutes = _rise(administrator, project, minutes=30, slower_by=PAST_CRITICAL)

    outcomes = await _judge_through(engine, store, project, minutes)

    opened_at = next(m for m, o in zip(minutes, outcomes, strict=True) if o.opened)
    assert opened_at - ONSET <= TIME_TO_A_FINDING
    (finding,) = await _findings(engine, project)
    assert finding.severity == "critical"
    assert finding.state == "open"


async def test_a_service_with_under_three_days_opens_nothing_and_says_until_when(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC14: learning opens nothing, whatever its values, and names its date."""
    _seed(administrator, project, [ONSET - timedelta(days=2)])
    await _baselines(engine, store, project)
    minutes = _rise(administrator, project, minutes=30, slower_by=PAST_CRITICAL)

    outcomes = await _judge_through(engine, store, project, minutes)

    assert sum(outcome.opened for outcome in outcomes) == 0
    assert await _findings(engine, project) == []
    slower = (await _state(engine, project)).signals["slower"]
    assert slower["verdict"] == "learning"
    assert slower["learning_until"] is not None
    assert slower["learning_until"] > ONSET.date().isoformat()


async def test_a_job_killed_before_its_commit_leaves_one_finding_once_rerun(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    project: AlertTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC25: the run that would open the finding dies after freezing its readings
    and before its commit. Nothing it decided survives; the next run decides again,
    and there is one finding and one reading per minute."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    minutes = _rise(administrator, project, minutes=SUSTAIN, slower_by=PAST_THE_MINIMUM)
    probe = await _judge_through(engine, store, project, minutes)
    opening = next(m for m, o in zip(minutes, probe, strict=True) if o.opened)
    # Start again from nothing, with the same readings already frozen once.
    async with engine.begin() as connection:
        for table in ("findings", "detection_state"):
            await connection.execute(
                text(f"DELETE FROM {table} WHERE org_id = :org_id"), {"org_id": project.org_id}
            )
    before = [m for m in minutes if m < opening]
    await _judge_through(engine, store, project, before)

    async def killed(*_: object) -> None:
        raise RuntimeError("killed between the readings and the commit")

    monkeypatch.setattr(project_job, "_record_run", killed)
    with pytest.raises(RuntimeError, match="killed"):
        await judge_project(engine, store, project.project_id, settled=opening, now=opening)
    assert await _findings(engine, project) == []
    assert (await _state(engine, project)).last_judged_minute == before[-1]
    monkeypatch.undo()

    rerun = await _judge_through(engine, store, project, [m for m in minutes if m >= opening])

    assert rerun[0].opened == 1
    assert len(await _findings(engine, project)) == 1
    readings = store.for_project(await _bound(engine, project)).readings(
        start=opening, end=opening + MINUTE
    )
    assert len(readings) == 1


async def test_two_jobs_at_once_judge_each_minute_once(
    store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """The lock: a second job for the project finds it held and does nothing, so
    each minute is judged once however many are queued (design, "Failure handling")."""
    first_engine = create_async_engine(POSTGRES_DSN.replace("://", "+asyncpg://", 1))
    second_engine = create_async_engine(POSTGRES_DSN.replace("://", "+asyncpg://", 1))
    try:
        _seed_four_weeks(administrator, project)
        await _baselines(first_engine, store, project)
        minutes = _rise(administrator, project, minutes=SUSTAIN, slower_by=PAST_THE_MINIMUM)
        judged = 0
        for minute in minutes:
            pair = await asyncio.gather(
                judge_project(first_engine, store, project.project_id, settled=minute, now=minute),
                judge_project(second_engine, store, project.project_id, settled=minute, now=minute),
            )
            judged += sum(outcome.minutes_judged for outcome in pair)
            # A job that lost the race judged nothing, rather than judging again.
            assert sum(outcome.skipped is None for outcome in pair) >= 1
        # Every minute judged, none twice; a skipped minute is caught up by the next run.
        assert judged == len(minutes)
        assert len(await _findings(first_engine, project)) == 1
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


async def test_a_service_moved_by_another_job_refuses_the_whole_commit(
    engine: AsyncEngine, project: AlertTenant
) -> None:
    """The compare-and-set, should two jobs ever both hold a project: the second to
    commit finds the minute already moved, and its transaction is rolled back."""
    minute = ONSET
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO detection_state "
                "(org_id, project_id, service_name, last_judged_minute) "
                "VALUES (:org_id, :project_id, :service, :minute)"
            ),
            {
                "org_id": project.org_id,
                "project_id": project.project_id,
                "service": SERVICE,
                "minute": minute,
            },
        )
    binding = await _bound(engine, project)
    stale = ServiceState(SERVICE, minute - MINUTE)

    with pytest.raises(StaleJudgementError):
        async with engine.begin() as connection:
            await project_job._advance(connection, binding, stale, minute + MINUTE, {})

    assert (await _state(engine, project)).last_judged_minute == minute


async def test_a_project_with_recent_traffic_is_handed_a_job(
    engine: AsyncEngine, project: AlertTenant
) -> None:
    """The coordinator's choice: traffic in the last 24 hours (the usage meter's
    `service_last_seen`), one job per project on the detection queue."""
    now = datetime.now(UTC)
    async with engine.connect() as connection:
        quiet = await active_projects(connection, now=now)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO service_last_seen (org_id, project_id, service_name, last_seen_at) "
                "VALUES (:org_id, :project_id, :service, :seen)"
            ),
            {
                "org_id": project.org_id,
                "project_id": project.project_id,
                "service": SERVICE,
                "seen": now - timedelta(hours=1),
            },
        )
    async with engine.connect() as connection:
        heard = await active_projects(connection, now=now)

    mine = ActiveProject(project.org_id, project.project_id)
    assert mine not in quiet
    assert mine in heard
    (request,) = [
        each
        for each in project_requests(heard, ONSET)
        if each.kwargs["project_id"] == str(project.project_id)
    ]
    assert request.queue == "detection"
    assert request.org_id == str(project.org_id)
    assert request.kwargs["settled_minute"] == ONSET.isoformat()


# ------------------------------------------------------------ Step 3 (T3.3)

ENDPOINTS: Final = {
    "GET /cart": (60, 0),
    "POST /pay": (20, 0),
    "GET /item": (15, 1),
    "GET /": (5, 0),
}


async def test_two_hours_on_two_signals_and_three_endpoints_are_one_finding(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC18, BR2: slower and failing more, with three endpoints failing, for two hours:
    one finding, whose worst values and title follow the deviation, closing once
    every key has recovered."""
    _seed_four_weeks(administrator, project, endpoints=ENDPOINTS, until=timedelta(minutes=150))
    await _baselines(engine, store, project)
    deviation = timedelta(hours=2)
    failing = {"GET /cart": (60, 12), "POST /pay": (20, 15), "GET /item": (15, 12), "GET /": (5, 0)}
    span = _span(ONSET - timedelta(minutes=30), ONSET + deviation + timedelta(minutes=30))
    _seed(
        administrator,
        project,
        span,
        slower_by={m: 3 for m in span if ONSET <= m < ONSET + deviation},
        endpoints={m: failing if ONSET <= m < ONSET + deviation else ENDPOINTS for m in span},
    )
    minutes = _span(ONSET - timedelta(minutes=10), ONSET + deviation + timedelta(minutes=20))

    outcomes = []
    for minute in minutes:
        if minute == ONSET + timedelta(minutes=100):
            await _baselines(engine, store, project, now=minute)
        outcomes.append(
            await judge_project(engine, store, project.project_id, settled=minute, now=minute)
        )

    assert sum(o.opened for o in outcomes) == 1
    assert sum(o.closed for o in outcomes) == 1
    (finding,) = await _findings(engine, project)
    assert finding.closed_reason == "recovered"
    assert finding.closed_at >= ONSET + deviation
    assert finding.started_at <= ONSET + timedelta(minutes=1)
    assert finding.title.startswith(f"{SERVICE} is failing ")
    assert finding.title.endswith(", and is slower")
    signals = {(s["signal"], s["endpoint"]) for s in finding.signals}
    assert ("slower", None) in signals
    endpoints = {endpoint for signal, endpoint in signals if signal == "failing"}
    assert {"POST /pay", "GET /item"} <= endpoints
    assert finding.signals[0]["signal"] == "failing"


async def test_nine_of_eleven_services_stopping_is_one_project_finding(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC21, FR15: one project finding, critical, and no finding for any service."""
    services = [f"svc-{index:02}" for index in range(11)]
    for service in services:
        _seed_four_weeks(administrator, project, service=service)
        stops = service < services[9]
        today = _span(
            ONSET - timedelta(minutes=30),
            ONSET + (-timedelta(minutes=1) if stops else timedelta(minutes=30)),
        )
        _seed(administrator, project, today, service=service)
    await _baselines(engine, store, project)
    minutes = _span(ONSET - timedelta(minutes=5), ONSET + timedelta(minutes=15))

    outcomes = await _judge_through(engine, store, project, minutes)

    (finding,) = await _findings(engine, project)
    assert finding.scope == "project" and finding.service_name is None
    assert finding.severity == "critical" and finding.state == "open"
    assert finding.title == (
        f"Traffic stopped arriving from 9 of 11 services at {ONSET:%H:%M} UTC. "
        "Check your collector or network"
    )
    assert sum(o.opened for o in outcomes) == 1
    opened_at = next(m for m, o in zip(minutes, outcomes, strict=True) if o.opened)
    assert opened_at - ONSET <= TIME_TO_A_FINDING


async def test_a_lower_sensitivity_closes_a_finding_its_minimum_no_longer_holds(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC30, FR18: about 1.7 times slower opens a finding at normal. Changed to low,
    whose minimum is 2 times, it closes after *recovery* with the rise unchanged, and
    the same rise opens nothing more."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    span = _span(ONSET - timedelta(minutes=30), ONSET + timedelta(minutes=60))
    _seed(administrator, project, span, slower_by={m: 3 for m in span if m >= ONSET})
    at_normal = _span(ONSET - timedelta(minutes=5), ONSET + timedelta(minutes=10))
    opened = await _judge_through(engine, store, project, at_normal)
    assert sum(o.opened for o in opened) == 1
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE projects SET detection_sensitivity = 'low' WHERE id = :id"),
            {"id": project.project_id},
        )
    switched = ONSET + timedelta(minutes=11)
    await _baselines(engine, store, project, now=switched)

    at_low = await _judge_through(
        engine, store, project, _span(switched, ONSET + timedelta(minutes=45))
    )

    assert sum(o.closed for o in at_low) == 1
    assert sum(o.opened for o in at_low) == 0
    (finding,) = await _findings(engine, project)
    assert finding.closed_reason == "recovered"
    assert finding.closed_at - switched <= timedelta(minutes=RECOVERY_MINUTES + 1)


def _last_thursday_noon() -> datetime:
    """A recent Thursday: three weekdays of history before it make it day 3 (D30)."""
    day = ONSET - timedelta(days=1)
    while day.weekday() != 3:
        day -= timedelta(days=1)
    return day


async def test_a_small_service_that_stops_on_day_three_is_critical_then_gone(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC41, FR32, D30: 2 requests a minute, history since Monday, stops at noon on
    Thursday: a critical finding within *time to a finding*. A day of silence later it
    closes as *service gone*."""
    onset = _last_thursday_noon()
    monday = onset.replace(hour=0) - timedelta(days=3)
    _seed(administrator, project, _span(monday, onset - MINUTE), requests=2)
    await _baselines(engine, store, project, now=onset - timedelta(minutes=5))
    minutes = _span(onset - timedelta(minutes=5), onset + timedelta(minutes=12))

    outcomes = await _judge_through(engine, store, project, minutes)

    opened_at = next(m for m, o in zip(minutes, outcomes, strict=True) if o.opened)
    assert opened_at - onset <= TIME_TO_A_FINDING
    (finding,) = await _findings(engine, project)
    assert finding.severity == "critical" and finding.state == "open"
    assert finding.signals[0]["signal"] == "traffic"
    assert finding.title == f"{SERVICE} stopped receiving requests at {onset:%H:%M} UTC"
    a_day_later = onset + timedelta(days=1, minutes=1)

    gone = await _judge_through(engine, store, project, [a_day_later])

    assert gone[0].closed == 1
    (finding,) = await _findings(engine, project)
    assert finding.closed_reason == "service_gone"


async def test_a_finding_that_cannot_be_judged_for_six_hours_closes_as_such(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """D20: a critical slowdown opens a finding; its service is then judged against no
    normal at all, and 6 hours later the finding closes as *could no longer be
    judged*, not as recovered."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    _seed(
        administrator,
        project,
        _span(ONSET - timedelta(minutes=30), ONSET + timedelta(minutes=10)),
        slower_by={ONSET + step * MINUTE: PAST_CRITICAL for step in range(11)},
    )
    opened = await _judge_through(
        engine, store, project, _span(ONSET, ONSET + timedelta(minutes=10))
    )
    assert sum(o.opened for o in opened) == 1
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM detection_baselines WHERE org_id = :org_id"),
            {"org_id": project.org_id},
        )
    last = ONSET + timedelta(minutes=10)
    closed_at = None
    # Two minutes a run, as a job catching up judges them.
    while closed_at is None and last < ONSET + timedelta(hours=7):
        last += 2 * MINUTE
        outcome = await judge_project(engine, store, project.project_id, settled=last, now=last)
        if outcome.closed:
            closed_at = last

    (finding,) = await _findings(engine, project)
    assert finding.closed_reason == "could_not_be_judged"
    assert closed_at is not None
    assert timedelta(hours=6) <= closed_at - ONSET <= timedelta(hours=6, minutes=15)


@pytest.mark.parametrize("scope", ["service", "project"])
async def test_a_second_open_finding_for_one_scope_is_refused_whichever_job_commits(
    project: AlertTenant, scope: str
) -> None:
    """The partial unique indexes: two jobs that both believe none is open race to
    insert; the second waits for the first, then is refused."""
    first_engine = create_async_engine(POSTGRES_DSN.replace("://", "+asyncpg://", 1))
    second_engine = create_async_engine(POSTGRES_DSN.replace("://", "+asyncpg://", 1))
    row = {
        "org_id": project.org_id,
        "project_id": project.project_id,
        "scope": scope,
        "service_name": SERVICE if scope == "service" else None,
        "severity": "warning",
        "muted": False,
        "title": "a race",
        "signals": "[]",
        "started_at": ONSET,
    }
    try:
        async with first_engine.connect() as first, second_engine.connect() as second:
            await first.begin()
            await second.begin()
            await first.execute(project_job._OPEN, row)
            racing = asyncio.create_task(second.execute(project_job._OPEN, row))
            await asyncio.sleep(0.2)
            assert not racing.done(), "the second insert waits on the first's transaction"
            await first.commit()
            with pytest.raises(IntegrityError, match="uq_findings_project_id"):
                await racing
            await second.rollback()
        assert len(await _findings(first_engine, project)) == 1
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


async def _bound(engine: AsyncEngine, tenant: AlertTenant) -> Any:
    async with engine.connect() as connection:
        binding = await bind_project(connection, tenant.project_id)
    assert binding is not None
    return binding

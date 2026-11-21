"""F19's reads against real ClickHouse (plan T1.3, T1.11, T2.1 to T2.3): exact counts
back from rows written by `slo_rows`, the lowered bounds sent with every read, and a
first test of `sumForEachMergeIf`, which the burn and latency reads will lean on.

Step 2: each of the four kinds gives its seeded count exactly (AC2), a latency target
counts at or under its boundary (AC3), left-out endpoints are subtracted and a capped
minute never goes negative (AC80, FR34), and a check counts only confirmed, measured
runs outside maintenance, once each however often a reading was delivered (AC2), with
missed runs lowering its coverage (AC67's coverage half) and its interval saying what it
allows when that is thin (AC11).

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest
from alert_fixtures import AlertTenant
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.client import Client
from live import require_stack
from nexovue_platform import slo
from slo_rows import (
    READINGS,
    Run,
    Traffic,
    minutes,
    traffic_rows,
    write_buckets,
    write_endpoint,
    write_runs,
    write_service,
)

from nexovue_scheduler.alerts.telemetry import TelemetryStore, connect
from nexovue_scheduler.slos import counts
from nexovue_scheduler.slos.job import Sources, Target, request_minutes
from nexovue_scheduler.slos.store import SLO_READ_SETTINGS, MinuteCounts, SloStore

READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
SERVICE: Final = "checkout"
UTC_ZONE: Final = ZoneInfo("UTC")
# Ahead of the real clock, so nothing the dev stack writes lands in the window.
START: Final = (datetime.now(UTC) + timedelta(days=2)).replace(
    hour=0, minute=0, second=0, microsecond=0
)


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 8123), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(READER_DSN),
    suite="F19's store suite",
    remedy="start the dev stack with `just dev`",
)


class _Recording:
    """The driver, with every query's settings kept, so a test can see what was sent."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self.settings: list[dict[str, Any]] = []

    def query(self, sql: str, *, parameters: dict[str, Any], settings: dict[str, Any]) -> Any:
        self.settings.append(settings)
        return self._client.query(sql, parameters=parameters, settings=settings)


def _store() -> tuple[SloStore, _Recording]:
    telemetry = connect(READER_DSN)
    recording = _Recording(telemetry._client)
    return SloStore(TelemetryStore(recording)), recording  # type: ignore[arg-type]


def test_ac1_a_million_requests_and_400_failures_read_back_exactly(
    administrator: Client, project: AlertTenant
) -> None:
    # 1,000 minutes, half by day and half by night: 600,000 and 400,000 requests.
    span = minutes(START + timedelta(hours=DAY_HOUR), 500) + minutes(
        START + timedelta(hours=NIGHT_HOUR), 500
    )
    traffic = Traffic(day_requests=1_200, night_requests=800, day_failures=0, night_failures=0)
    counts = traffic_rows(span, traffic, UTC_ZONE)
    for minute in span[:400]:
        requests, _ = counts[minute]
        counts[minute] = (requests, 1)
    write_service(administrator, project, SERVICE, counts)
    store, _ = _store()

    read = store.for_project(project.org_id, project.project_id).service_minutes(
        [SERVICE], start=START, end=START + timedelta(days=2)
    )[SERVICE]

    total = sum(minute.requests for minute in read.values())
    failures = sum(minute.failures for minute in read.values())
    assert (total, failures, len(read)) == (1_000_000, 400, 1_000)
    assert slo.allowance_left(Decimal("99.9"), total, failures) == 600
    night = START + timedelta(hours=NIGHT_HOUR)
    assert read[night].requests == 800
    assert read[START + timedelta(hours=DAY_HOUR)].requests == 1_200


def test_a_read_binds_its_project_and_sees_no_other(
    administrator: Client, project: AlertTenant
) -> None:
    write_service(administrator, project, SERVICE, {START: (7, 0)})
    store, _ = _store()
    other = store.for_project(project.org_id, "00000000-0000-0000-0000-000000000000")
    assert other.service_minutes([SERVICE], start=START, end=START + timedelta(hours=1)) == {}


def test_every_read_is_sent_with_the_lowered_bounds(
    administrator: Client, project: AlertTenant
) -> None:
    write_service(administrator, project, SERVICE, {START: (3, 0)})
    store, recording = _store()
    store.for_project(project.org_id, project.project_id).service_minutes(
        [SERVICE], start=START, end=START + timedelta(hours=1)
    )
    assert recording.settings == [SLO_READ_SETTINGS]
    assert recording.settings[0]["max_execution_time"] == 5
    assert recording.settings[0]["max_memory_usage"] == 256 * 1024 * 1024


def test_sum_for_each_merge_if_splits_buckets_by_window_in_one_statement(
    administrator: Client, project: AlertTenant
) -> None:
    """The design's open question (T1.3): ClickHouse 26.3 must accept the combinators in
    this order, or the burn and latency reads take the fallback of one branch per window."""
    early, late = START, START + timedelta(minutes=30)
    buckets = {early: [5] + [0] * 63, late: [0, 7] + [0] * 62}
    write_buckets(administrator, project, SERVICE, buckets)

    result = administrator.query(
        """
        SELECT sumForEachMergeIf(latency_buckets, minute >= {split:DateTime}) AS recent,
               sumForEachMerge(latency_buckets) AS whole
        FROM service_stats_1m
        WHERE tenant_id = {tenant_id:String} AND project_id = {project_id:String}
          AND service_name = {service:String}
          AND minute >= {from_minute:DateTime} AND minute < {to_minute:DateTime}
        """,
        parameters={
            "tenant_id": str(project.org_id),
            "project_id": str(project.project_id),
            "service": SERVICE,
            "split": late,
            "from_minute": early,
            "to_minute": early + timedelta(hours=1),
        },
    )
    recent, whole = result.result_rows[0]
    assert list(recent)[:2] == [0, 7]
    assert list(whole)[:2] == [5, 7]


DAY_HOUR: Final = 9
NIGHT_HOUR: Final = 22


# ------------------------------------------------------------ Step 2: four kinds

HEALTH: Final = "GET /health"
ORDERS: Final = "POST /orders"


def _target(kind: str, **fields: Any) -> Target:
    return Target(
        id=uuid.uuid4(),
        kind=kind,
        service_name=fields.pop("service_name", SERVICE),
        first_event_at=START,
        definition_version=1,
        evaluated_minute=None,
        last_event_minute=None,
        **fields,
    )


def test_ac3_a_latency_target_counts_requests_at_or_under_its_boundary(
    administrator: Client, project: AlertTenant
) -> None:
    index = slo.boundary_at_or_under(500.0)
    assert index is not None and round(slo.boundary_ms(index), 1) == 487.2
    buckets = [0] * slo.BUCKET_COUNT
    buckets[index - 1], buckets[index], buckets[index + 1] = 11, 13, 17
    write_buckets(
        administrator, project, SERVICE, {START: buckets, START + timedelta(minutes=1): buckets}
    )
    store, recording = _store()
    reader = store.for_project(project.org_id, project.project_id)

    latency = reader.service_latency([SERVICE], start=START, end=START + timedelta(hours=1))
    target = _target(slo.SERVICE_LATENCY, boundary_index=index)
    counted = request_minutes(target, Sources({}, latency, {}, {}))

    assert sum(minute.requests for minute in counted.values()) == 82
    assert sum(minute.requests - minute.failures for minute in counted.values()) == 48
    assert recording.settings == [SLO_READ_SETTINGS]


def test_ac2_an_endpoint_target_reads_its_own_endpoint_and_count_columns_only(
    administrator: Client, project: AlertTenant
) -> None:
    write_endpoint(administrator, project, SERVICE, ORDERS, {START: (40, 3)})
    write_endpoint(administrator, project, SERVICE, HEALTH, {START: (6, 0)})
    write_endpoint(administrator, project, "cart", ORDERS, {START: (9, 9)})
    store, _ = _store()
    reader = store.for_project(project.org_id, project.project_id)

    read = reader.endpoint_minutes([(SERVICE, ORDERS)], start=START, end=START + timedelta(hours=1))

    assert read == {(SERVICE, ORDERS): {START: MinuteCounts(40, 3)}}


def test_ac80_left_out_probes_count_nothing_and_a_capped_minute_is_not_negative(
    administrator: Client, project: AlertTenant
) -> None:
    """Seeded probe traffic: `/health` left out of a service availability target counts
    no probe request; a minute of probes alone is quiet, so a dead service still reads
    **No recent requests**; and probes in a minute the service cap dropped subtract
    nothing (FR34)."""
    first, probes_only, capped = START, START + timedelta(minutes=1), START + timedelta(minutes=2)
    write_service(administrator, project, SERVICE, {first: (100, 5), probes_only: (6, 0)})
    write_endpoint(
        administrator,
        project,
        SERVICE,
        HEALTH,
        {first: (6, 0), probes_only: (6, 0), capped: (6, 0)},
    )
    store, _ = _store()
    reader = store.for_project(project.org_id, project.project_id)
    target = _target(slo.SERVICE_AVAILABILITY, left_out_endpoints=(HEALTH,))
    span = {"start": START, "end": START + timedelta(hours=1)}

    sources = Sources(
        services=reader.service_minutes([SERVICE], **span),
        latency={},
        endpoints=reader.endpoint_minutes([(SERVICE, HEALTH)], **span),
        checks={},
    )
    counted = request_minutes(target, sources)

    assert counted == {
        first: MinuteCounts(94, 5),
        probes_only: MinuteCounts(0, 0),
    }
    assert counts.last_event(counted) == first


@pytest.fixture
def readings(project: AlertTenant) -> None:
    delete_after_run(READINGS, str(project.org_id))


def test_ac2_a_check_counts_confirmed_measured_runs_once_each(
    administrator: Client, project: AlertTenant, readings: None
) -> None:
    """Eight minutes of runs: an overruled attempt beside its confirmed retry, one in
    maintenance, one `prober_error`, one `misconfigured`, one down, and one delivered
    twice, beside another check's run. Eight are confirmed and seen; five count, four
    of them up; one ran in maintenance."""
    check = str(uuid.uuid4())
    at = [START + timedelta(minutes=offset) for offset in range(8)]
    runs = [
        Run(at[0]),
        Run(at[1], up=False, outcome="down", final=False),
        Run(at[1], up=True, attempt=2),
        Run(at[2], maintenance=True),
        Run(at[3], up=False, outcome="prober_error"),
        Run(at[4], up=False, outcome="misconfigured"),
        Run(at[5], up=False, outcome="down"),
        Run(at[6]),
        Run(at[7]),
    ]
    write_runs(administrator, project, check, runs)
    write_runs(administrator, project, check, [Run(at[7])])
    write_runs(administrator, project, str(uuid.uuid4()), [Run(at[0])])
    store, recording = _store()

    read = store.for_project(project.org_id, project.project_id).check_runs(
        [check], start=START, end=START + timedelta(hours=1)
    )[check]

    assert sum(minute.seen for minute in read.values()) == 8
    assert sum(minute.counted for minute in read.values()) == 5
    assert sum(minute.up for minute in read.values()) == 4
    assert sum(minute.maintenance for minute in read.values()) == 1
    assert recording.settings == [SLO_READ_SETTINGS]


@pytest.mark.parametrize("interval", [60, 300, 3_600])
def test_ac67_a_stopped_prober_lowers_a_check_s_coverage_through_missed_runs(
    administrator: Client, project: AlertTenant, readings: None, interval: int
) -> None:
    """A check that ran for the first half of a day and then stopped: half its runs are
    missed, so the day is not measured, at each interval."""
    check = str(uuid.uuid4())
    expected = 86_400 // interval
    every = timedelta(seconds=interval)
    write_runs(
        administrator, project, check, [Run(START + every * n) for n in range(expected // 2)]
    )
    store, _ = _store()
    read = store.for_project(project.org_id, project.project_id).check_runs(
        [check], start=START, end=START + timedelta(days=1)
    )[check]

    row = counts.check_day_row(
        read,
        day_start=START,
        day_end=START + timedelta(days=1),
        through=START + timedelta(days=1),
        first_event_at=START,
        interval_seconds=interval,
        locations=1,
    )
    day = slo.DayCounts(
        good=row.good,
        total=row.total,
        lost_minutes=0,
        expected_runs=row.expected_runs,
        missed_runs=row.missed_runs,
        maintenance_runs=row.maintenance_runs,
    )
    figures = slo.window_figures(
        {START.date(): day},
        window_days=7,
        first_event_at=START,
        now=START + timedelta(days=1),
        zone=UTC_ZONE,
    )
    assert (row.expected_runs, row.missed_runs) == (expected, expected - expected // 2)
    assert figures.coverage is not None and figures.coverage < slo.COVERAGE_TO_JUDGE


def test_ac11_a_thin_check_target_reads_too_little_traffic_from_its_runs(
    administrator: Client, project: AlertTenant, readings: None
) -> None:
    """A 5-minute check over a day holds 288 runs: 99.9% allows none, so the target is
    **Too little traffic**, and what its interval allows over 28 days is 8."""
    check = str(uuid.uuid4())
    every = timedelta(minutes=5)
    write_runs(administrator, project, check, [Run(START + every * n) for n in range(288)])
    store, _ = _store()
    read = store.for_project(project.org_id, project.project_id).check_runs(
        [check], start=START, end=START + timedelta(days=1)
    )[check]
    total = sum(minute.counted for minute in read.values())

    assert total == 288
    assert slo.allowed_failures(Decimal("99.9"), total) < slo.THIN_ALLOWED
    assert slo.allowed_failures(Decimal("99.9"), slo.expected_runs(300, window_days=28)) == 8

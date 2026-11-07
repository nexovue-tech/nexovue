"""F19's reads against real ClickHouse (plan T1.3, T1.11): exact counts back from rows
written by `slo_rows`, the lowered bounds sent with every read, and a first test of
`sumForEachMergeIf`, which the burn and latency reads will lean on.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from alert_fixtures import AlertTenant
from clickhouse_connect.driver.client import Client
from live import require_stack
from nexovue_platform import slo
from slo_rows import Traffic, minutes, traffic_rows, write_buckets, write_service

from nexovue_scheduler.alerts.telemetry import TelemetryStore, connect
from nexovue_scheduler.slos.store import SLO_READ_SETTINGS, SloStore

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

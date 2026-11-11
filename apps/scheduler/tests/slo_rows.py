"""Exact rows for F19's live suites (plan T1.11, T2.3): a service's and an endpoint's
minutes written straight into the rollups, as the worker writes them, with day and night
traffic, and a check's `synthetic.check.status` readings in `metrics_gauge`, as the
prober publishes them, so a test knows every count it should read back.

A reading carries the attributes the uptime rule reads (`nexovue_platform.readings`):
outcome, whether it is the confirmed attempt, and whether it ran in maintenance. The
prober writes those as an OTLP string, bool and int (`publish._run_attributes`); the
JSON here holds the same types. Proved by the reader that counts them (T2.3,
`test_slo_store_live.py`), which is why the founder moved it there on 2026-11-08.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from alert_fixtures import AlertTenant
from clickhouse_connect.driver.client import Client
from nexovue_platform.readings import METRIC_STATUS, UP_VALUE
from nexovue_platform.slo import BUCKET_COUNT

SERVICE_ROLLUP: Final = "service_stats_1m"
ENDPOINT_ROLLUP: Final = "endpoint_stats_1m"
READINGS: Final = "metrics_gauge"
PROBER_SERVICE: Final = "nexovue-prober"
MINUTE: Final = timedelta(minutes=1)
_INSERT_BATCH: Final = 500
# Day traffic from 08:00 to 20:00 local, night outside it (AC16, AC92: seed data
# must hold both).
DAY_STARTS_HOUR: Final = 8
NIGHT_STARTS_HOUR: Final = 20


@dataclass(frozen=True, slots=True)
class Traffic:
    """Requests and failures a minute, by day and by night."""

    day_requests: int
    night_requests: int
    day_failures: int = 0
    night_failures: int = 0

    def at(self, minute: datetime, zone: ZoneInfo) -> tuple[int, int]:
        hour = minute.astimezone(zone).hour
        if DAY_STARTS_HOUR <= hour < NIGHT_STARTS_HOUR:
            return self.day_requests, self.day_failures
        return self.night_requests, self.night_failures


def minutes(first: datetime, count: int) -> list[datetime]:
    return [first + MINUTE * offset for offset in range(count)]


def traffic_rows(
    span: Iterable[datetime], traffic: Traffic, zone: ZoneInfo
) -> dict[datetime, tuple[int, int]]:
    """Each minute's (requests, failures) for `traffic` over `span`."""
    return {minute: traffic.at(minute, zone) for minute in span}


def write_service(
    client: Client,
    tenant: AlertTenant,
    service: str,
    counts: Mapping[datetime, tuple[int, int]],
) -> None:
    """One `service_stats_1m` row a minute, every request in the first bucket."""
    rows = [
        f"('{tenant.org_id}','{tenant.project_id}','{service}',{int(minute.timestamp())},"
        f"{requests},{failures},{_buckets(requests)})"
        for minute, (requests, failures) in counts.items()
    ]
    for batch in _batches(rows):
        client.command(
            f"""
            INSERT INTO {SERVICE_ROLLUP}
                (tenant_id, project_id, service_name, minute, request_count, error_count,
                 latency_buckets)
            SELECT tenant_id, project_id, service_name, toDateTime(epoch), requests, errors,
                   arrayReduce('sumForEachState', [buckets])
            FROM values(
                'tenant_id String, project_id String, service_name String, epoch UInt32,
                 requests UInt64, errors UInt64, buckets Array(UInt32)',
                {batch}
            )
            """
        )


def write_buckets(
    client: Client,
    tenant: AlertTenant,
    service: str,
    buckets: Mapping[datetime, Sequence[int]],
) -> None:
    """`service_stats_1m` rows with the given bucket counts and no failures."""
    rows = [
        f"('{tenant.org_id}','{tenant.project_id}','{service}',{int(minute.timestamp())},"
        f"{sum(counts)},0,{list(counts)})"
        for minute, counts in buckets.items()
    ]
    for batch in _batches(rows):
        client.command(
            f"""
            INSERT INTO {SERVICE_ROLLUP}
                (tenant_id, project_id, service_name, minute, request_count, error_count,
                 latency_buckets)
            SELECT tenant_id, project_id, service_name, toDateTime(epoch), requests, errors,
                   arrayReduce('sumForEachState', [buckets])
            FROM values(
                'tenant_id String, project_id String, service_name String, epoch UInt32,
                 requests UInt64, errors UInt64, buckets Array(UInt32)',
                {batch}
            )
            """
        )


def write_endpoint(
    client: Client,
    tenant: AlertTenant,
    service: str,
    endpoint: str,
    counts: Mapping[datetime, tuple[int, int]],
) -> None:
    """One `endpoint_stats_1m` row a minute for one `SERVER` endpoint."""
    rows = [
        f"('{tenant.org_id}','{tenant.project_id}','{service}','SERVER','{endpoint}',"
        f"{int(minute.timestamp())},{requests},{failures})"
        for minute, (requests, failures) in counts.items()
    ]
    for batch in _batches(rows):
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
                {batch}
            )
            """
        )


@dataclass(frozen=True, slots=True)
class Run:
    """One check run's status reading, with the attributes the uptime rule reads."""

    at: datetime
    up: bool = True
    outcome: str = "up"
    final: bool = True
    maintenance: bool = False
    attempt: int = 1
    location: str = "local"


def write_runs(client: Client, tenant: AlertTenant, check_id: str, runs: Sequence[Run]) -> None:
    """One `synthetic.check.status` row per run, as the prober publishes it. Writing a
    run twice is a redelivery, which the reader must count once (AC2)."""
    rows = [_reading_row(tenant, check_id, run) for run in runs]
    for batch in _batches(rows):
        client.command(
            f"""
            INSERT INTO {READINGS}
                (timestamp, start_timestamp, received_at, tenant_id, project_id, service_name,
                 metric_name, metric_unit, resource_attrs, attrs, value)
            VALUES {batch}
            """
        )


def _reading_row(tenant: AlertTenant, check_id: str, run: Run) -> str:
    stamp = _sql_string(run.at.strftime("%Y-%m-%d %H:%M:%S.000"))
    resource = {"synthetic": {"check": {"id": check_id, "type": "http"}}}
    attributes = {
        "synthetic": {
            "check": {
                "outcome": run.outcome,
                "run": {"id": f"{check_id}-{int(run.at.timestamp())}-{run.attempt}"},
                "attempt": run.attempt,
                "final": run.final,
                "maintenance": run.maintenance,
                "location": run.location,
            }
        }
    }
    fields = (
        stamp,
        stamp,
        stamp,
        _sql_string(str(tenant.org_id)),
        _sql_string(str(tenant.project_id)),
        _sql_string(PROBER_SERVICE),
        _sql_string(METRIC_STATUS),
        _sql_string("1"),
        _sql_string(json.dumps(resource)),
        _sql_string(json.dumps(attributes)),
        repr(float(UP_VALUE if run.up else 0)),
    )
    return "(" + ", ".join(fields) + ")"


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _buckets(requests: int) -> list[int]:
    return [requests, *([0] * (BUCKET_COUNT - 1))]


def _batches(rows: list[str]) -> Iterable[str]:
    for start in range(0, len(rows), _INSERT_BATCH):
        yield ",".join(rows[start : start + _INSERT_BATCH])

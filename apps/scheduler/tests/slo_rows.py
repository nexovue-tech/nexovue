"""Exact rows for F19's live suites (plan T1.11): a service's and an endpoint's minutes
written straight into the rollups, as the worker writes them, with day and night
traffic, so a test knows every count it should read back.

`metrics_gauge` readings for check targets join this helper with their reader in T2.3.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from alert_fixtures import AlertTenant
from clickhouse_connect.driver.client import Client
from nexovue_platform.slo import BUCKET_COUNT

SERVICE_ROLLUP: Final = "service_stats_1m"
ENDPOINT_ROLLUP: Final = "endpoint_stats_1m"
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


def _buckets(requests: int) -> list[int]:
    return [requests, *([0] * (BUCKET_COUNT - 1))]


def _batches(rows: list[str]) -> Iterable[str]:
    for start in range(0, len(rows), _INSERT_BATCH):
        yield ",".join(rows[start : start + _INSERT_BATCH])

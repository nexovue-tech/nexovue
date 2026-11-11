"""F19's reads of the telemetry store (design, *Reads*; ADR-012).

**Through the scheduler's one reader.** Every statement here is a module constant
that binds the tenant and the project, run by `alerts.telemetry`'s reader, which
refuses one that does not. This module holds no driver: `test_tenant_chokepoint_
structure.py` allows the raw client in `alerts.telemetry` and not here.

**Under lowered bounds.** A read may take 5 seconds and 256 MiB on the server, as
detection's do (`detection/store.py`), not the shared 30 seconds and gigabyte: SLO
work shares `scheduler-light`'s capped seats (D66), and a slow read must not hold one.

**One statement per source, for the whole project** (design, *What each job does*):
a service's counts; its latency buckets; the named endpoints, both an endpoint
target's own and the ones a service target leaves out; and a check's runs, by the
rule the Synthetics screen counts uptime by (`nexovue_platform.readings`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform import readings
from nexovue_platform.tenant_statements import lowered_read_settings

from nexovue_scheduler.alerts.telemetry import TelemetryStore, TenantScopedReader, connect

SLO_READ_SETTINGS: Final = lowered_read_settings(
    max_execution_time=5,
    max_memory_usage=256 * 1024 * 1024,
    max_bytes_before_external_group_by=128 * 1024 * 1024,
)

# Each named service's requests and failures, minute by minute, in [from, to): a day's
# row, or the last day for the job. Only count columns, so the buckets are not read.
SLO_SERVICE_DAY_SQL: Final = """
SELECT service_name,
       toUnixTimestamp(minute) AS minute_epoch,
       sum(request_count) AS requests,
       sum(error_count) AS errors
FROM service_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND service_name IN {services:Array(String)}
  AND minute >= {from_minute:DateTime}
  AND minute < {to_minute:DateTime}
GROUP BY service_name, minute
"""


# A service's latency buckets, minute by minute: the counts a latency target splits at
# its boundary. Only the buckets and the request count are read.
SLO_SERVICE_LATENCY_SQL: Final = """
SELECT service_name,
       toUnixTimestamp(minute) AS minute_epoch,
       sum(request_count) AS requests,
       sumForEachMerge(latency_buckets) AS buckets
FROM service_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND service_name IN {services:Array(String)}
  AND minute >= {from_minute:DateTime}
  AND minute < {to_minute:DateTime}
GROUP BY service_name, minute
"""

# Named endpoints' requests and failures, minute by minute: count columns only, never
# the quantile state. Service, kind and name are all bound, so the sort key
# (`service_name, span_kind, span_name, minute`) skips to them (design, *Performance*).
# The kinds are the inbound ones the worker counts (`derive/endpoints.go`).
SLO_ENDPOINT_MINUTES_SQL: Final = """
SELECT service_name,
       span_name,
       toUnixTimestamp(minute) AS minute_epoch,
       sum(request_count) AS requests,
       sum(error_count) AS errors
FROM endpoint_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND service_name IN {services:Array(String)}
  AND span_kind IN ('SERVER', 'CONSUMER')
  AND span_name IN {endpoints:Array(String)}
  AND minute >= {from_minute:DateTime}
  AND minute < {to_minute:DateTime}
GROUP BY service_name, span_name, minute
"""

# The Synthetics screen's rule, bound here as module constants, the only values a
# statement may embed (`test_tenant_chokepoint_structure.py`).
_METRIC_STATUS: Final = readings.METRIC_STATUS
_CHECK_ID: Final = readings.CHECK_ID
_COUNTED_RUN: Final = readings.COUNTED_RUN
_READING_ATTRIBUTES: Final = readings.READING_ATTRIBUTES
_ONE_ROW_PER_READING: Final = readings.ONE_ROW_PER_READING
_UP_VALUE: Final = readings.UP_VALUE

# Each check's confirmed runs, minute by minute (AC2): `seen` is every confirmed run,
# `counted` those the uptime rule counts (no overruled attempt, maintenance run or
# unmeasured outcome), `up` the counted ones that were up. A redelivered reading is
# collapsed to one first. `received_at` is bounded too, for partition pruning.
SLO_CHECK_RUNS_SQL: Final = f"""
SELECT check_id,
       toUnixTimestamp(toStartOfMinute(timestamp)) AS minute_epoch,
       count() AS seen,
       countIf(maintenance_flag = 'true') AS maintenance,
       countIf({_COUNTED_RUN}) AS counted,
       countIf({_COUNTED_RUN} AND value = {_UP_VALUE}) AS up
FROM (
    SELECT timestamp,
           {_CHECK_ID} AS check_id,
           metric_name,
           any(value) AS value,{_READING_ATTRIBUTES}
    FROM metrics_gauge
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND metric_name = '{_METRIC_STATUS}'
      AND {_CHECK_ID} IN {{checks:Array(String)}}
      AND timestamp >= {{from_time:DateTime64(3)}}
      AND timestamp < {{to_time:DateTime64(3)}}
      AND received_at >= {{received_from:DateTime64(3)}}
      AND received_at < {{received_to:DateTime64(3)}}
    {_ONE_ROW_PER_READING}
)
WHERE final_flag != 'false'
GROUP BY check_id, minute_epoch
"""

# `metrics_gauge` is partitioned on our receive clock: a day each side, as the API's
# reads widen theirs (`nexovue_api/window.py`, `RECEIVE_CLOCK_SLACK`).
RECEIVED_SLACK: Final = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class MinuteCounts:
    requests: int
    failures: int


@dataclass(frozen=True, slots=True)
class LatencyMinute:
    """A service's minute: its requests and its 64 latency bucket counts."""

    requests: int
    buckets: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CheckMinute:
    """A check's confirmed runs in one minute (see `SLO_CHECK_RUNS_SQL`)."""

    seen: int
    counted: int
    up: int
    maintenance: int


# A source's minutes: minute (UTC) to its counts. A minute with no row is absent.
SourceMinutes = Mapping[datetime, MinuteCounts]


class SloReader:
    """F19's statements for one project, bound to it by the reader it wraps."""

    def __init__(self, reader: TenantScopedReader) -> None:
        self._reader = reader

    def service_minutes(
        self, services: Iterable[str], *, start: datetime, end: datetime
    ) -> dict[str, dict[datetime, MinuteCounts]]:
        """Each named service's counted minutes in [start, end)."""
        named = sorted(set(services))
        if not named:
            return {}
        rows = self._reader._query(
            SLO_SERVICE_DAY_SQL,
            {"services": named, "from_minute": start, "to_minute": end},
            settings=SLO_READ_SETTINGS,
        )
        return _by_service(rows)

    def service_latency(
        self, services: Iterable[str], *, start: datetime, end: datetime
    ) -> dict[str, dict[datetime, LatencyMinute]]:
        """Each named service's latency buckets, minute by minute, in [start, end)."""
        named = sorted(set(services))
        if not named:
            return {}
        rows = self._reader._query(
            SLO_SERVICE_LATENCY_SQL,
            {"services": named, "from_minute": start, "to_minute": end},
            settings=SLO_READ_SETTINGS,
        )
        found: dict[str, dict[datetime, LatencyMinute]] = defaultdict(dict)
        for row in rows:
            found[str(row["service_name"])][_minute(row)] = LatencyMinute(
                requests=int(row["requests"]), buckets=tuple(int(n) for n in row["buckets"])
            )
        return dict(found)

    def endpoint_minutes(
        self, endpoints: Iterable[tuple[str, str]], *, start: datetime, end: datetime
    ) -> dict[tuple[str, str], dict[datetime, MinuteCounts]]:
        """Each named (service, endpoint)'s counted minutes in [start, end). One read
        for the union of names; a pair nobody asked for is dropped here."""
        wanted = set(endpoints)
        if not wanted:
            return {}
        rows = self._reader._query(
            SLO_ENDPOINT_MINUTES_SQL,
            {
                "services": sorted({service for service, _ in wanted}),
                "endpoints": sorted({endpoint for _, endpoint in wanted}),
                "from_minute": start,
                "to_minute": end,
            },
            settings=SLO_READ_SETTINGS,
        )
        found: dict[tuple[str, str], dict[datetime, MinuteCounts]] = defaultdict(dict)
        for row in rows:
            pair = (str(row["service_name"]), str(row["span_name"]))
            if pair in wanted:
                found[pair][_minute(row)] = MinuteCounts(
                    requests=int(row["requests"]), failures=int(row["errors"])
                )
        return dict(found)

    def check_runs(
        self, check_ids: Iterable[str], *, start: datetime, end: datetime
    ) -> dict[str, dict[datetime, CheckMinute]]:
        """Each named check's confirmed runs, minute by minute, in [start, end)."""
        named = sorted(set(check_ids))
        if not named:
            return {}
        rows = self._reader._query(
            SLO_CHECK_RUNS_SQL,
            {
                "checks": named,
                "from_time": start,
                "to_time": end,
                "received_from": start - RECEIVED_SLACK,
                "received_to": end + RECEIVED_SLACK,
            },
            settings=SLO_READ_SETTINGS,
        )
        found: dict[str, dict[datetime, CheckMinute]] = defaultdict(dict)
        for row in rows:
            found[str(row["check_id"])][_minute(row)] = CheckMinute(
                seen=int(row["seen"]),
                counted=int(row["counted"]),
                up=int(row["up"]),
                maintenance=int(row["maintenance"]),
            )
        return dict(found)


def _minute(row: Mapping[str, Any]) -> datetime:
    return datetime.fromtimestamp(int(row["minute_epoch"]), UTC)


def _by_service(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[datetime, MinuteCounts]]:
    found: dict[str, dict[datetime, MinuteCounts]] = defaultdict(dict)
    for row in rows:
        found[str(row["service_name"])][_minute(row)] = MinuteCounts(
            requests=int(row["requests"]), failures=int(row["errors"])
        )
    return dict(found)


class SloStore:
    """The telemetry store, handed out one project at a time."""

    def __init__(self, telemetry: TelemetryStore) -> None:
        self._telemetry = telemetry

    def for_project(self, org_id: object, project_id: object) -> SloReader:
        return SloReader(self._telemetry.for_project(org_id, project_id))


def connect_store(dsn: str) -> SloStore:
    """F19's reads through the reader account at `dsn` (`CLICKHOUSE_DSN`)."""
    return SloStore(connect(dsn))

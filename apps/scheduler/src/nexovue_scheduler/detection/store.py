"""Detection's only way into the telemetry store (ADR-023, design "Security, tenancy, and privacy").

**Bound to one project.** What leaves this module is built by `for_project` from
a `ProjectBinding`, whose organization is read from the `projects` row rather than
taken from a caller. Every read binds both the tenant and the project, and runs
under bounds lowered for a job with a 20-second budget.

**The one writer.** `detection_readings` is written here and nowhere else in
nexovue, as `nexovue_detection_writer`, which can insert into that table only.
Its row type has no tenant or project field: the writer fills both in from the
binding, so a reading cannot be filed under another project. The structure test
(`apps/api/tests/test_tenant_chokepoint_structure.py`) allows a telemetry insert
in this module alone.
"""

from __future__ import annotations

import math
import uuid
from array import array
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import astuple, dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import unquote, urlsplit

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from nexovue_platform.detection import SHIFT_COLUMNS, SHIFT_READINGS
from nexovue_platform.telemetry.logging import get_logger
from nexovue_platform.tenant_statements import (
    READ_SETTINGS,
    ProjectFilterMissingError,
    TenantFilterMissingError,
    binds_project,
    binds_tenant,
    lowered_read_settings,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from nexovue_scheduler.detection.method import BUCKET_COUNT, EndpointCounts, Minute
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

READINGS_TABLE: Final = "detection_readings"
_MINUTE: Final = timedelta(minutes=1)

# A project job has 20 seconds in all (design, "Where it runs"); one read may not
# take the shared server's 30 seconds or its gigabyte. The spill threshold stays at
# half the memory cap, as the shared bounds have it.
DETECTION_READ_SETTINGS: Final = lowered_read_settings(
    max_execution_time=5,
    max_memory_usage=256 * 1024 * 1024,
    max_bytes_before_external_group_by=128 * 1024 * 1024,
)

# One insert per project job, a few a second at the cost target: the server batches them
# into fewer parts, and the job waits until its readings are written (design, *Performance
# and scale*). Stated because ClickHouse's default changed in 26.2.
READINGS_INSERT_SETTINGS: Final = {"async_insert": 1, "wait_for_async_insert": 1}

NO_STORE: Final = (
    "CLICKHOUSE_DSN or CLICKHOUSE_DETECTION_WRITER_DSN is not set for the scheduler; "
    "anomaly detection judges nothing on this deployment"
)


@dataclass(frozen=True, slots=True)
class Reading:
    """One judged minute of one service: migration 000036's columns, less the
    tenant, project, retention and version, which the writer supplies."""

    service_name: str
    # The judged minute, UTC.
    minute: datetime
    window_minutes: int
    window_requests: int
    window_errors: int
    window_p95_ms: float | None
    window_above_threshold: int
    endpoint_failures: dict[str, tuple[int, int]]
    traffic_mode: str
    traffic_days: int
    traffic_median: float | None
    traffic_spread: float | None
    traffic_verdict: str
    failing_mode: str
    failing_days: int
    failing_median: float | None
    failing_spread: float | None
    failing_burst_height: float | None
    failing_burst_minutes: int
    failing_verdict: str
    slower_mode: str
    slower_days: int
    slower_median: float | None
    slower_spread: float | None
    slower_threshold_ms: float | None
    slower_verdict: str


_KEY_COLUMNS: Final = ("service_name", "minute")
_VALUE_COLUMNS: Final = tuple(
    field.name for field in fields(Reading) if field.name not in _KEY_COLUMNS
)
_LATEST_VALUES: Final = ", ".join(_VALUE_COLUMNS)
_WRITTEN_COLUMNS: Final = (
    "tenant_id",
    "project_id",
    "derived_retention_days",
    "version",
    *_KEY_COLUMNS,
    *_VALUE_COLUMNS,
)

# The newest version of each minute, so one rewritten after a crash is read once
# before a merge as well as after (migration 000036). One tuple rather than an
# argMax per column: an aggregate skips a NULL argument, which would return an
# older version's statistic where the newest had none.
READINGS_SQL: Final = f"""
SELECT
    service_name,
    toUnixTimestamp(minute) AS minute_epoch,
    argMax(tuple({_LATEST_VALUES}), version) AS latest
FROM detection_readings
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND minute >= {{from_minute:DateTime}}
  AND minute < {{to_minute:DateTime}}
GROUP BY service_name, minute
ORDER BY service_name, minute
"""

# The columns a shift's ratio is read from (`nexovue_platform.detection.shift_ratio`,
# FR21), each by its name, and how many of a service's newest readings it takes.
_SHIFT_COLUMNS: Final = ", ".join(SHIFT_COLUMNS)
_SHIFT_FIELDS: Final = ", ".join(
    f"latest.{index + 1} AS {column}" for index, column in enumerate(SHIFT_COLUMNS)
)
_SHIFT_LIMIT: Final = SHIFT_READINGS
# One service's newest readings in [from, to), newest first.
SHIFT_READINGS_SQL: Final = f"""
SELECT {_SHIFT_FIELDS}
FROM (
    SELECT minute, argMax(tuple({_SHIFT_COLUMNS}), version) AS latest
    FROM detection_readings
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND service_name = {{service:String}}
      AND minute >= {{from_minute:DateTime}}
      AND minute < {{to_minute:DateTime}}
    GROUP BY minute
)
ORDER BY minute DESC
LIMIT {_SHIFT_LIMIT}
"""

# Each service's minutes in [from, to): what a window, or a pool, is built from.
# `sumForEachMerge` returns the 64 bucket counts summed over every row of a minute.
_MINUTE_COLUMNS: Final = """
    service_name,
    toUnixTimestamp(minute) AS minute_epoch,
    sum(request_count) AS requests,
    sum(error_count) AS errors,
    sumForEachMerge(latency_buckets) AS buckets
"""
WINDOW_MINUTES_SQL: Final = f"""
SELECT {_MINUTE_COLUMNS}
FROM service_stats_1m
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND minute >= {{from_minute:DateTime}}
  AND minute < {{to_minute:DateTime}}
GROUP BY service_name, minute
"""
SERVICE_MINUTES_SQL: Final = f"""
SELECT {_MINUTE_COLUMNS}
FROM service_stats_1m
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND service_name IN {{services:Array(String)}}
  AND minute >= {{from_minute:DateTime}}
  AND minute < {{to_minute:DateTime}}
GROUP BY service_name, minute
"""
# Each endpoint's counted minutes in [from, to), from the same inbound spans as the
# service's (FR2). An endpoint is named by its span: a SERVER and a CONSUMER span of
# one name are counted together.
ENDPOINT_WINDOW_SQL: Final = """
SELECT service_name, span_name, toUnixTimestamp(minute) AS minute_epoch,
       sum(request_count) AS requests, sum(error_count) AS errors
FROM endpoint_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND minute >= {from_minute:DateTime}
  AND minute < {to_minute:DateTime}
GROUP BY service_name, span_name, minute
"""
ENDPOINT_MINUTES_SQL: Final = """
SELECT service_name, span_name, toUnixTimestamp(minute) AS minute_epoch,
       sum(request_count) AS requests, sum(error_count) AS errors
FROM endpoint_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND service_name IN {services:Array(String)}
  AND minute >= {from_minute:DateTime}
  AND minute < {to_minute:DateTime}
GROUP BY service_name, span_name, minute
"""
# Requests and failures only, over the 28 days a burst profile reads (D17).
SERVICE_COUNTS_SQL: Final = """
SELECT service_name, toUnixTimestamp(minute) AS minute_epoch,
       sum(request_count) AS requests, sum(error_count) AS errors
FROM service_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND service_name IN {services:Array(String)}
  AND minute >= {from_minute:DateTime}
  AND minute < {to_minute:DateTime}
GROUP BY service_name, minute
"""
# The failing normal each minute was judged by, from its newest reading: with the
# minute's counts it says again whether the window passed the base test (D17).
FAILING_NORMALS_SQL: Final = """
SELECT service_name, minute_epoch, latest.1 AS rate, latest.2 AS wander
FROM (
    SELECT
        service_name,
        toUnixTimestamp(minute) AS minute_epoch,
        argMax(tuple(failing_median, failing_spread, window_minutes), version) AS latest
    FROM detection_readings
    WHERE tenant_id = {tenant_id:String}
      AND project_id = {project_id:String}
      AND service_name IN {services:Array(String)}
      AND minute >= {from_minute:DateTime}
      AND minute < {to_minute:DateTime}
    GROUP BY service_name, minute
)
WHERE latest.3 = {window_minutes:UInt8} AND latest.1 IS NOT NULL AND latest.2 IS NOT NULL
"""
# Each judged minute's per-endpoint window counts, from its newest reading: the
# heights an endpoint's own burst profile is taken from (D18 per endpoint).
ENDPOINT_WINDOWS_SQL: Final = """
SELECT service_name, minute_epoch, latest.1 AS endpoints
FROM (
    SELECT
        service_name,
        toUnixTimestamp(minute) AS minute_epoch,
        argMax(tuple(endpoint_failures, window_minutes), version) AS latest
    FROM detection_readings
    WHERE tenant_id = {tenant_id:String}
      AND project_id = {project_id:String}
      AND service_name IN {services:Array(String)}
      AND minute >= {from_minute:DateTime}
      AND minute < {to_minute:DateTime}
    GROUP BY service_name, minute
)
WHERE latest.2 = {window_minutes:UInt8}
"""
# FR3's *slower* evidence: each endpoint's requests and p95 in one window and in the
# same window a week earlier. The quantile levels are the column's own (000008).
ENDPOINT_LATENCY_SQL: Final = """
SELECT span_name,
       sumIf(request_count, minute >= {from_minute:DateTime}) AS requests,
       (quantilesMergeIf(0.5, 0.95, 0.99)(
           latency_quantiles, minute >= {from_minute:DateTime}))[2] AS p95,
       sumIf(request_count, minute < {week_to:DateTime}) AS week_requests,
       (quantilesMergeIf(0.5, 0.95, 0.99)(
           latency_quantiles, minute < {week_to:DateTime}))[2] AS week_p95
FROM endpoint_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND service_name = {service:String}
  AND ((minute >= {from_minute:DateTime} AND minute < {to_minute:DateTime})
       OR (minute >= {week_from:DateTime} AND minute < {week_to:DateTime}))
GROUP BY span_name
"""
_WEEK: Final = timedelta(days=7)

# When each service was first counted since `since`: where its history starts.
FIRST_SEEN_SQL: Final = """
SELECT service_name, toUnixTimestamp(min(minute)) AS first_epoch
FROM service_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND minute >= {since:DateTime}
GROUP BY service_name
"""

# The organization comes from the project's own row, never from the caller, and
# with it the derived retention the readings expire at (design 2.1, m6).
_PROJECT_BINDING: Final = text(
    """
    SELECT org_id, derived_retention_days FROM project_retention
    WHERE project_id = :project_id
    """
)


# A reading's version: Postgres's clock in microseconds, one clock however many hosts run
# jobs. A re-run after a crash reads it later than the crashed run did, so its reading
# wins `argMax` even where the crashed run's host clock was ahead (design, *Readings*).
_READING_VERSION: Final = text(
    "SELECT CAST(floor(extract(epoch FROM clock_timestamp()) * 1000000) AS bigint)"
)


async def reading_version(connection: AsyncConnection) -> int:
    """The version the readings written next carry, from Postgres's clock."""
    version = await connection.scalar(_READING_VERSION)
    return int(version)


@dataclass(frozen=True, slots=True)
class ProjectBinding:
    """One live project, its organization and its derived retention, read from Postgres."""

    org_id: str
    project_id: str
    derived_retention_days: int


async def bind_project(connection: AsyncConnection, project_id: uuid.UUID) -> ProjectBinding | None:
    """The project as Postgres has it, or None when it is gone."""
    row = (await connection.execute(_PROJECT_BINDING, {"project_id": project_id})).one_or_none()
    if row is None:
        return None
    return ProjectBinding(
        org_id=str(row.org_id),
        project_id=str(project_id),
        derived_retention_days=int(row.derived_retention_days),
    )


class DetectionStore:
    """The two clients, held here and nowhere else: the reader account for reads,
    and the writer account, which can insert into `detection_readings` only."""

    def __init__(self, reader: Client, writer: Client) -> None:
        self._reader = reader
        self._writer = writer

    def for_project(self, binding: ProjectBinding) -> ProjectDetection:
        return ProjectDetection(self._reader, self._writer, binding)


class ProjectDetection:
    """Reads and writes one project's detection telemetry. Built per project job,
    never shared across projects."""

    def __init__(self, reader: Client, writer: Client, binding: ProjectBinding) -> None:
        self._reader = reader
        self._writer = writer
        self._binding = binding

    def readings(self, *, start: datetime, end: datetime) -> list[Reading]:
        """Every service's newest reading for each minute in [start, end)."""
        rows = self._query(READINGS_SQL, {"from_minute": start, "to_minute": end})
        return [
            Reading(
                service_name,
                datetime.fromtimestamp(int(minute_epoch), UTC),
                *_values(latest),
            )
            for service_name, minute_epoch, latest in rows
        ]

    def shift_readings(
        self, service: str, *, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """One service's last `SHIFT_READINGS` readings in [start, end), newest first."""
        rows = self._query(
            SHIFT_READINGS_SQL, {"service": service, "from_minute": start, "to_minute": end}
        )
        return [dict(zip(SHIFT_COLUMNS, row, strict=True)) for row in rows]

    def window_minutes(
        self, *, start: datetime, end: datetime
    ) -> dict[str, dict[datetime, Minute]]:
        """Every service's counted minutes in [start, end). A minute with no row had
        no inbound requests."""
        return _by_service(
            self._blocks(WINDOW_MINUTES_SQL, {"from_minute": start, "to_minute": end})
        )

    def service_minutes(
        self, services: Sequence[str], *, start: datetime, end: datetime
    ) -> dict[str, dict[datetime, Minute]]:
        """The named services' counted minutes in [start, end)."""
        if not services:
            return {}
        return _by_service(
            self._blocks(
                SERVICE_MINUTES_SQL,
                {"services": list(services), "from_minute": start, "to_minute": end},
            )
        )

    def endpoint_minutes(
        self, services: Sequence[str] | None, *, start: datetime, end: datetime
    ) -> dict[str, dict[datetime, dict[str, EndpointCounts]]]:
        """Each endpoint's counted minutes in [start, end), by service and minute:
        every service's when `services` is None, else only the named ones'."""
        window = {"from_minute": start, "to_minute": end}
        if services is None:
            rows = self._query(ENDPOINT_WINDOW_SQL, window)
        elif not services:
            return {}
        else:
            rows = self._query(ENDPOINT_MINUTES_SQL, {**window, "services": list(services)})
        found: dict[str, dict[datetime, dict[str, EndpointCounts]]] = {}
        for service, endpoint, epoch, requests, errors in rows:
            minute = datetime.fromtimestamp(int(epoch), UTC)
            minutes = found.setdefault(str(service), {}).setdefault(minute, {})
            minutes[str(endpoint)] = (int(requests), int(errors))
        return found

    def service_counts(
        self, services: Sequence[str], *, start: datetime, end: datetime
    ) -> dict[str, MinuteCounts]:
        """The named services' requests and failures for each counted minute in [start, end).

        Streamed in blocks into compact arrays: 28 days of a service is 40,320 minutes, and
        as Python tuples keyed by datetime ten services' took about 100 MiB (AC29)."""
        if not services:
            return {}
        found: dict[str, MinuteCounts] = {}
        parameters = {"services": list(services), "from_minute": start, "to_minute": end}
        for services_column, epochs, requests, errors in self._blocks(
            SERVICE_COUNTS_SQL, parameters
        ):
            for service, epoch, n, e in zip(services_column, epochs, requests, errors, strict=True):
                counts = found.get(service)
                if counts is None:
                    counts = found[service] = MinuteCounts(start, end)
                counts.add(datetime.fromtimestamp(int(epoch), UTC), int(n), int(e))
        return found

    def failing_normals(
        self, services: Sequence[str], *, start: datetime, end: datetime, window_minutes: int
    ) -> dict[str, dict[datetime, tuple[float, float]]]:
        """The normal rate and wander each minute of `window_minutes` was judged by."""
        if not services:
            return {}
        rows = self._query(
            FAILING_NORMALS_SQL,
            {
                "services": list(services),
                "from_minute": start,
                "to_minute": end,
                "window_minutes": window_minutes,
            },
        )
        found: dict[str, dict[datetime, tuple[float, float]]] = {}
        for service, epoch, rate, wander in rows:
            minute = datetime.fromtimestamp(int(epoch), UTC)
            found.setdefault(str(service), {})[minute] = (float(rate), float(wander))
        return found

    def endpoint_windows(
        self, services: Sequence[str], *, start: datetime, end: datetime, window_minutes: int
    ) -> dict[str, dict[datetime, dict[str, EndpointCounts]]]:
        """Each judged minute's endpoint window counts, over the request floor."""
        if not services:
            return {}
        rows = self._query(
            ENDPOINT_WINDOWS_SQL,
            {
                "services": list(services),
                "from_minute": start,
                "to_minute": end,
                "window_minutes": window_minutes,
            },
        )
        found: dict[str, dict[datetime, dict[str, EndpointCounts]]] = {}
        for service, epoch, endpoints in rows:
            minute = datetime.fromtimestamp(int(epoch), UTC)
            found.setdefault(str(service), {})[minute] = {
                str(name): _endpoint_counts(counts) for name, counts in endpoints.items()
            }
        return found

    def endpoint_latency(
        self, service: str, *, start: datetime, end: datetime
    ) -> dict[str, EndpointLatency]:
        """Each of the service's endpoints in [start, end) and the same window a week
        earlier: its requests and p95 in each, None where it had no latency."""
        rows = self._query(
            ENDPOINT_LATENCY_SQL,
            {
                "service": service,
                "from_minute": start,
                "to_minute": end,
                "week_from": start - _WEEK,
                "week_to": end - _WEEK,
            },
        )
        return {
            str(name): EndpointLatency(
                requests=int(requests),
                p95_ms=_finite(p95),
                week_requests=int(week_requests),
                week_p95_ms=_finite(week_p95),
            )
            for name, requests, p95, week_requests, week_p95 in rows
        }

    def first_seen(self, *, since: datetime) -> dict[str, datetime]:
        """Each service counted since `since`, and its first counted minute."""
        rows = self._query(FIRST_SEEN_SQL, {"since": since})
        return {str(service): datetime.fromtimestamp(int(epoch), UTC) for service, epoch in rows}

    def write(self, readings: Sequence[Reading], *, version: int) -> None:
        """Freeze these readings under this project at `version`, from
        `reading_version`. A minute written again wins by a higher version
        (migration 000036)."""
        if not readings:
            return
        for reading in readings:
            if reading.minute.utcoffset() is None:
                raise ValueError(f"a reading's minute must be timezone-aware: {reading.minute}")
        bound = (
            self._binding.org_id,
            self._binding.project_id,
            self._binding.derived_retention_days,
            version,
        )
        self._writer.insert(
            READINGS_TABLE,
            [(*bound, *astuple(reading)) for reading in readings],
            column_names=list(_WRITTEN_COLUMNS),
            settings=READINGS_INSERT_SETTINGS,
        )

    def _query(self, sql: str, parameters: dict[str, Any]) -> list[tuple[Any, ...]]:
        result = self._reader.query(
            sql, parameters=self._bound(sql, parameters), settings=DETECTION_READ_SETTINGS
        )
        return [tuple(row) for row in result.result_rows]

    def _blocks(self, sql: str, parameters: dict[str, Any]) -> Iterator[Sequence[Sequence[Any]]]:
        """The result one block of columns at a time, so no read is held whole as rows."""
        stream = self._reader.query_column_block_stream(
            sql, parameters=self._bound(sql, parameters), settings=DETECTION_READ_SETTINGS
        )
        with stream:
            yield from stream

    def _bound(self, sql: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if not binds_tenant(sql):
            raise TenantFilterMissingError(sql)
        if not binds_project(sql):
            raise ProjectFilterMissingError(sql)
        return parameters | {
            "tenant_id": self._binding.org_id,
            "project_id": self._binding.project_id,
        }


@dataclass(frozen=True, slots=True)
class EndpointLatency:
    """One endpoint's requests and p95 in a window and in the same window a week before."""

    requests: int
    p95_ms: float | None
    week_requests: int
    week_p95_ms: float | None


def _finite(value: Any) -> float | None:
    """A merged quantile of no values is NaN: no latency, not a number."""
    found = float(value)
    return None if math.isnan(found) else found


class MinuteCounts(Mapping[datetime, EndpointCounts]):
    """One service's (requests, failures) by minute over [start, end), held as two arrays
    of machine integers rather than a tuple and a datetime per minute. A minute never
    added had no row, and is absent."""

    def __init__(self, start: datetime, end: datetime) -> None:
        self._start = start
        length = max(0, (end - start) // _MINUTE)
        self._requests = array("Q", bytes(8 * length))
        self._failures = array("Q", bytes(8 * length))
        self._counted = bytearray(length)

    def add(self, minute: datetime, requests: int, failures: int) -> None:
        index = self._index(minute)
        if index is None:
            raise ValueError(f"{minute} is outside [{self._start}, {self._end})")
        self._requests[index] = requests
        self._failures[index] = failures
        self._counted[index] = 1

    @property
    def _end(self) -> datetime:
        return self._start + len(self._counted) * _MINUTE

    def _index(self, minute: datetime) -> int | None:
        offset = minute - self._start
        index = offset // _MINUTE
        if offset % _MINUTE or not 0 <= index < len(self._counted):
            return None
        return index

    def get(self, minute: datetime, default: Any = None) -> Any:
        index = self._index(minute)
        if index is None or not self._counted[index]:
            return default
        return self._requests[index], self._failures[index]

    def __getitem__(self, minute: datetime) -> EndpointCounts:
        found: EndpointCounts | None = self.get(minute)
        if found is None:
            raise KeyError(minute)
        return found

    def __iter__(self) -> Iterator[datetime]:
        for index, counted in enumerate(self._counted):
            if counted:
                yield self._start + index * _MINUTE

    def __len__(self) -> int:
        return sum(self._counted)


def _by_service(blocks: Iterable[Sequence[Sequence[Any]]]) -> dict[str, dict[datetime, Minute]]:
    found: dict[str, dict[datetime, Minute]] = {}
    rows = (row for block in blocks for row in zip(*block, strict=True))
    for service, epoch, requests, errors, buckets in rows:
        counts = tuple(int(count) for count in buckets)
        if len(counts) != BUCKET_COUNT:
            # An empty state merges to an empty array; anything else is a schema change.
            if counts:
                raise ValueError(f"a minute of {service} has {len(counts)} latency buckets")
            counts = (0,) * BUCKET_COUNT
        minute = datetime.fromtimestamp(int(epoch), UTC)
        found.setdefault(str(service), {})[minute] = Minute(int(requests), int(errors), counts)
    return found


def _endpoint_counts(counts: Any) -> EndpointCounts:
    """The driver returns a named tuple as a dict, or as a tuple in a nested one."""
    if isinstance(counts, dict):
        return int(counts["requests"]), int(counts["failures"])
    requests, failures = counts
    return int(requests), int(failures)


def _values(latest: Sequence[Any]) -> list[Any]:
    """A reading's values in `Reading`'s types. The driver returns the named
    tuple of each endpoint's counts as a dict."""
    values = list(latest)
    failures = _VALUE_COLUMNS.index("endpoint_failures")
    values[failures] = {
        str(name): (int(counts["requests"]), int(counts["failures"]))
        for name, counts in values[failures].items()
    }
    return values


def client_at(dsn: str, *, settings: dict[str, Any] | None = None) -> Client:
    """A client at `clickhouse://user:password@host:port/database`, for this
    package's modules that hold one."""
    parts = urlsplit(dsn)
    return clickhouse_connect.get_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=unquote(parts.username or ""),
        password=unquote(parts.password or ""),
        database=parts.path.lstrip("/") or "default",
        secure=parts.scheme in ("https", "clickhouses"),
        settings=settings,
    )


def connect(reader_dsn: str, writer_dsn: str) -> DetectionStore:
    return DetectionStore(client_at(reader_dsn, settings=READ_SETTINGS), client_at(writer_dsn))


_process_store: DetectionStore | None = None


def store_for_process() -> DetectionStore | None:
    """This worker child's store, opened on first use from its settings; None, with
    an ERROR, where the deployment has not given the scheduler both accounts."""
    global _process_store
    if _process_store is None:
        settings = get_settings()
        _process_store = open_store(
            settings.clickhouse_dsn, settings.clickhouse_detection_writer_dsn
        )
    return _process_store


def open_store(reader_dsn: str, writer_dsn: str) -> DetectionStore | None:
    """The store, or None with an ERROR naming what is missing: detection that
    cannot write must not look like detection that found nothing."""
    if not reader_dsn or not writer_dsn:
        logger.error(NO_STORE)
        return None
    return connect(reader_dsn, writer_dsn)

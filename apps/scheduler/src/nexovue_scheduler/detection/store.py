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

import time
import uuid
from collections.abc import Sequence
from dataclasses import astuple, dataclass, fields
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import unquote, urlsplit

import clickhouse_connect
from clickhouse_connect.driver.client import Client
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

from nexovue_scheduler.detection.method import BUCKET_COUNT, Minute
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

READINGS_TABLE: Final = "detection_readings"

# A project job has 20 seconds in all (design, "Where it runs"); one read may not
# take the shared server's 30 seconds or its gigabyte. The spill threshold stays at
# half the memory cap, as the shared bounds have it.
DETECTION_READ_SETTINGS: Final = lowered_read_settings(
    max_execution_time=5,
    max_memory_usage=256 * 1024 * 1024,
    max_bytes_before_external_group_by=128 * 1024 * 1024,
)

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

    def window_minutes(
        self, *, start: datetime, end: datetime
    ) -> dict[str, dict[datetime, Minute]]:
        """Every service's counted minutes in [start, end). A minute with no row had
        no inbound requests."""
        rows = self._query(WINDOW_MINUTES_SQL, {"from_minute": start, "to_minute": end})
        return _by_service(rows)

    def service_minutes(
        self, services: Sequence[str], *, start: datetime, end: datetime
    ) -> dict[str, dict[datetime, Minute]]:
        """The named services' counted minutes in [start, end)."""
        if not services:
            return {}
        rows = self._query(
            SERVICE_MINUTES_SQL,
            {"services": list(services), "from_minute": start, "to_minute": end},
        )
        return _by_service(rows)

    def first_seen(self, *, since: datetime) -> dict[str, datetime]:
        """Each service counted since `since`, and its first counted minute."""
        rows = self._query(FIRST_SEEN_SQL, {"since": since})
        return {str(service): datetime.fromtimestamp(int(epoch), UTC) for service, epoch in rows}

    def write(self, readings: Sequence[Reading]) -> None:
        """Freeze these readings under this project. A minute written again wins
        by a higher version (migration 000036)."""
        if not readings:
            return
        for reading in readings:
            if reading.minute.utcoffset() is None:
                raise ValueError(f"a reading's minute must be timezone-aware: {reading.minute}")
        version = time.time_ns()
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
        )

    def _query(self, sql: str, parameters: dict[str, Any]) -> list[tuple[Any, ...]]:
        if not binds_tenant(sql):
            raise TenantFilterMissingError(sql)
        if not binds_project(sql):
            raise ProjectFilterMissingError(sql)
        bound = parameters | {
            "tenant_id": self._binding.org_id,
            "project_id": self._binding.project_id,
        }
        result = self._reader.query(sql, parameters=bound, settings=DETECTION_READ_SETTINGS)
        return [tuple(row) for row in result.result_rows]


def _by_service(rows: Sequence[tuple[Any, ...]]) -> dict[str, dict[datetime, Minute]]:
    found: dict[str, dict[datetime, Minute]] = {}
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

"""F19's reads of the telemetry store (design, *Reads*; ADR-012).

**Through the scheduler's one reader.** Every statement here is a module constant
that binds the tenant and the project, run by `alerts.telemetry`'s reader, which
refuses one that does not. This module holds no driver: `test_tenant_chokepoint_
structure.py` allows the raw client in `alerts.telemetry` and not here.

**Under lowered bounds.** A read may take 5 seconds and 256 MiB on the server, as
detection's do (`detection/store.py`), not the shared 30 seconds and gigabyte: SLO
work shares `scheduler-light`'s capped seats (D66), and a slow read must not hold one.

**Availability first.** Step 1 reads `service_stats_1m`'s counts; latency, endpoint
and check sources join in Step 2.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

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


@dataclass(frozen=True, slots=True)
class MinuteCounts:
    requests: int
    failures: int


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


def _by_service(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[datetime, MinuteCounts]]:
    found: dict[str, dict[datetime, MinuteCounts]] = defaultdict(dict)
    for row in rows:
        minute = datetime.fromtimestamp(int(row["minute_epoch"]), UTC)
        found[str(row["service_name"])][minute] = MinuteCounts(
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

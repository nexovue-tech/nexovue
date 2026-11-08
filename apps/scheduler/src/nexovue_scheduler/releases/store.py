"""The release sync's read of the telemetry store (F28 design 3, step 4; ADR-012).

**Through the scheduler's one reader.** The statement is a module constant binding the
tenant and the project, run by `alerts.telemetry`'s reader, which refuses one that does
not. This module holds no driver, as F19's `slos/store.py` does not; the structure test
(`test_tenant_chokepoint_structure.py`) holds every statement here to both bindings.

**Under lowered bounds.** A run may take 10 seconds and 256 MiB on the server, so a
slow ClickHouse holds one of the queue's two seats briefly and gives it back (AC72).

**What it reads.** Each service's requests per minute and version, for the walk: no
errors and no buckets, which the comparison reads in the API. No version and the
worker's fold are left out here: neither can become a release (FR17, FR18).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from nexovue_platform.releases import NO_VERSION, OTHER_VERSION
from nexovue_platform.tenant_statements import lowered_read_settings

from nexovue_scheduler.alerts.telemetry import TelemetryStore, TenantScopedReader, connect

RELEASE_READ_SETTINGS: Final = lowered_read_settings(
    max_execution_time=10,
    max_memory_usage=256 * 1024 * 1024,
    max_bytes_before_external_group_by=128 * 1024 * 1024,
)
# Bound into the statement as module constants, the only values it may embed.
_NO_VERSION: Final = NO_VERSION
_OTHER_VERSION: Final = OTHER_VERSION

# Requests per service, minute and version in [from, to), oldest first. Version sits
# after minute in the sort key (`000038`), so one project's minutes are one range.
RELEASE_VERSION_MINUTES_SQL: Final = f"""
SELECT service_name,
       toUnixTimestamp(minute) AS minute_epoch,
       version,
       sum(request_count) AS requests
FROM service_versions_1m
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND minute >= {{from_minute:DateTime}}
  AND minute < {{to_minute:DateTime}}
  AND version NOT IN ('{_NO_VERSION}', '{_OTHER_VERSION}')
GROUP BY service_name, minute, version
HAVING requests > 0
ORDER BY minute, service_name, version
"""


@dataclass(frozen=True, slots=True)
class VersionCount:
    service_name: str
    minute: datetime
    version: str
    requests: int


class ReleaseReader:
    """The sync's statement for one project, bound to it by the reader it wraps."""

    def __init__(self, reader: TenantScopedReader) -> None:
        self._reader = reader

    def version_minutes(self, *, start: datetime, end: datetime) -> list[VersionCount]:
        rows = self._reader._query(
            RELEASE_VERSION_MINUTES_SQL,
            {"from_minute": start, "to_minute": end},
            settings=RELEASE_READ_SETTINGS,
        )
        return [
            VersionCount(
                service_name=str(row["service_name"]),
                minute=datetime.fromtimestamp(int(row["minute_epoch"]), UTC),
                version=str(row["version"]),
                requests=int(row["requests"]),
            )
            for row in rows
        ]


class ReleaseStore:
    """The telemetry store, handed out one project at a time."""

    def __init__(self, telemetry: TelemetryStore) -> None:
        self._telemetry = telemetry

    def for_project(self, org_id: object, project_id: object) -> ReleaseReader:
        return ReleaseReader(self._telemetry.for_project(org_id, project_id))


def connect_store(dsn: str) -> ReleaseStore:
    """The sync's reads through the reader account at `dsn` (`CLICKHOUSE_DSN`)."""
    return ReleaseStore(connect(dsn))

"""The evidence job's reads of the telemetry store (F3 design, *Reads*; plan T1.3).

**Through the scheduler's one reader.** Every statement is a module constant binding the
tenant and the project, run by `alerts.telemetry`'s reader, which refuses one that does
not. This module holds no driver; `test_tenant_chokepoint_structure.py` holds every
statement here to both bindings (`evidence` is a project-bound package).

**Under lowered bounds.** 10 seconds and 256 MiB each (guesses, as detection's), so a
slow ClickHouse holds one of the queue's two seats briefly. A read that times out is a
`could_not_read` absence, never "none" (AC64).

**What it reads.** This service's calls to others (`service_edges_1m`, server door
where the project has one, A63), its traffic (`service_stats_1m`), error groups not
seen in the hour before (A62), and hosts' disk and memory where they carry
`host.name` (FR52). The error read keeps a group's id, a checked exception type,
services and counts: never a message (A47).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform import readings
from nexovue_platform.evidence import (
    EXCEPTION_TYPE_PATTERN,
    MAX_EDGES_READ,
    MAX_ERROR_GROUPS_PER_SERVICE,
)
from nexovue_platform.tenant_statements import lowered_read_settings

from nexovue_scheduler.alerts.telemetry import TelemetryStore, TenantScopedReader, connect
from nexovue_scheduler.evidence.rules import (
    NEW_ERROR_LOOK_BACK,
    EdgeFact,
    ErrorGroup,
    HostReading,
    Traffic,
)

EVIDENCE_READ_SETTINGS: Final = lowered_read_settings(
    max_execution_time=10,
    max_memory_usage=256 * 1024 * 1024,
    max_bytes_before_external_group_by=128 * 1024 * 1024,
)
# The most groups one error read keeps, newest-heavy first (design, *Reads*).
MOST_NEW_GROUPS: Final = 2000
# Bound into the statements as module constants, the only values they may embed.
_QUANTILES: Final = readings.QUANTILE_ARGUMENTS
_P95: Final = readings.P95_INDEX
_SDK_PATH: Final = readings.SDK_FINGERPRINT_PATH
_MESSAGE_TEMPLATE: Final = readings.MESSAGE_TEMPLATE_SQL
_GROUP_ID: Final = readings.GROUP_ID_SQL
# One more than the bound, so a read can tell it was cut (FR51).
_EDGES_FETCHED: Final = MAX_EDGES_READ + 1
_SERVER_DOOR: Final = "server"
_BROWSER_DOOR: Final = "browser"
_EXCEPTION_TYPE: Final = re.compile(EXCEPTION_TYPE_PATTERN)

# One service's calls to each callee in the window and the equal one before, per door,
# the busiest first, at most the bound per door inside the statement (FR51).
EDGES_SQL: Final = f"""
SELECT server_service AS callee,
       door,
       max(server_peer_attr) AS callee_attr,
       max(server_peer_kind) AS callee_kind,
       sumIf(call_count, minute >= {{during_from:DateTime}}) AS calls_during,
       sumIf(call_count, minute < {{during_from:DateTime}}) AS calls_before,
       sumIf(error_count, minute >= {{during_from:DateTime}}) AS errors_during,
       sumIf(error_count, minute < {{during_from:DateTime}}) AS errors_before,
       quantilesMergeIf({_QUANTILES})(latency_quantiles, minute >= {{during_from:DateTime}})[{_P95}]
           AS p95_during,
       quantilesMergeIf({_QUANTILES})(latency_quantiles, minute < {{during_from:DateTime}})[{_P95}]
           AS p95_before
FROM service_edges_1m
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND client_service IN {{callers:Array(String)}}
  AND minute >= {{before_from:DateTime}}
  AND minute < {{during_to:DateTime}}
  AND door IN ('{_SERVER_DOOR}', '{_BROWSER_DOOR}')
GROUP BY client_service, server_service, door
ORDER BY calls_during DESC, callee
LIMIT {_EDGES_FETCHED} BY door
"""
# The same, with the caller named, for a project finding's services.
PROJECT_EDGES_SQL: Final = EDGES_SQL.replace(
    "SELECT server_service AS callee,", "SELECT client_service AS caller, server_service AS callee,"
)

# Requests a minute: the lead-in, and the hour before it (rule 7).
TRAFFIC_SQL: Final = """
SELECT sumIf(request_count, minute >= {lead_from:DateTime}) AS lead_requests,
       sumIf(request_count, minute < {lead_from:DateTime}) AS before_requests,
       countIf(minute < {lead_from:DateTime}) AS before_rows
FROM service_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND service_name = {service:String}
  AND minute >= {before_from:DateTime}
  AND minute < {lead_to:DateTime}
"""

# Error groups first received in the window and never in the hour before it (A62).
# Not `alerts/telemetry.py`'s `ERROR_GROUPS_SQL`, which orders oldest first for a
# continuing sweep: here a limit must not cut the newest groups.
NEW_ERROR_GROUPS_SQL: Final = f"""
SELECT group_id,
       any(exception_type) AS exception_type,
       groupUniqArray(20)(service_name) AS services,
       countIf(received >= {{window_from:DateTime64(9)}}) AS occurrences,
       minIf(received, received >= {{window_from:DateTime64(9)}}) AS first_seen,
       countIf(received < {{window_from:DateTime64(9)}}) AS earlier
FROM (
    SELECT {_GROUP_ID} AS group_id, exception_type, service_name, received
    FROM (
        SELECT exception_type, service_name, sdk_fingerprint, received,
               {_MESSAGE_TEMPLATE} AS message_template
        FROM (
            SELECT exception_type, exception_message,
                   ifNull(span_attrs.{_SDK_PATH}.:String, '') AS sdk_fingerprint,
                   service_name, received_at AS received
            FROM spans
            WHERE tenant_id = {{tenant_id:String}}
              AND project_id = {{project_id:String}}
              AND exception_type != ''
              AND received_at >= {{look_back_from:DateTime64(9)}}
              AND received_at < {{window_to:DateTime64(9)}}
            UNION ALL
            SELECT exception_type, exception_message,
                   ifNull(log_attrs.{_SDK_PATH}.:String, '') AS sdk_fingerprint,
                   service_name, received_at AS received
            FROM logs
            WHERE tenant_id = {{tenant_id:String}}
              AND project_id = {{project_id:String}}
              AND exception_type != ''
              AND received_at >= {{look_back_from:DateTime64(9)}}
              AND received_at < {{window_to:DateTime64(9)}}
        )
    )
)
GROUP BY group_id
HAVING earlier = 0 AND occurrences > 0
ORDER BY occurrences DESC, group_id
LIMIT {MOST_NEW_GROUPS}
"""

# Each host's highest memory use in the window, and each of its filesystems' use at its
# last reading, as used over the sum of every state (`conventions.py`: no
# `system.filesystem.limit` is emitted). Per filesystem, so a device mounted twice is
# never summed twice. A reading with no `host.name` comes back with an empty host and is
# never a host fact (FR52).
HOSTS_SQL: Final = """
SELECT host, measure, max(utilization) AS utilization, max(at) AS at
FROM (
    SELECT toString(resource_attrs.host.name) AS host, 'memory' AS measure,
           max(value) AS utilization, max(timestamp) AS at
    FROM metrics_gauge
    WHERE tenant_id = {tenant_id:String}
      AND project_id = {project_id:String}
      AND metric_name = 'system.memory.utilization'
      AND toString(attrs.state) IN ('used', '')
      AND timestamp >= {from_time:DateTime64(3)}
      AND timestamp < {to_time:DateTime64(3)}
    GROUP BY host
    UNION ALL
    SELECT host, 'disk' AS measure, if(total > 0, used / total, 0) AS utilization, at
    FROM (
        SELECT host, filesystem,
               sumIf(last_value, state = 'used') AS used,
               sum(last_value) AS total,
               max(last_at) AS at
        FROM (
            SELECT toString(resource_attrs.host.name) AS host,
                   concat(toString(attrs.device), '|', toString(attrs.mountpoint)) AS filesystem,
                   toString(attrs.state) AS state,
                   argMax(value, timestamp) AS last_value, max(timestamp) AS last_at
            FROM metrics_sum
            WHERE tenant_id = {tenant_id:String}
              AND project_id = {project_id:String}
              AND metric_name = 'system.filesystem.usage'
              AND timestamp >= {from_time:DateTime64(3)}
              AND timestamp < {to_time:DateTime64(3)}
            GROUP BY host, filesystem, state
        )
        GROUP BY host, filesystem
    )
)
GROUP BY host, measure
"""


@dataclass(frozen=True, slots=True)
class Edges:
    edges: tuple[EdgeFact, ...]
    # A project finding's: (caller, edge).
    by_caller: tuple[tuple[str, EdgeFact], ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class Hosts:
    readings: tuple[HostReading, ...]
    # Whether any host reading in the window named its host.
    names_present: bool


def exception_type_shown(value: str) -> str | None:
    """A sender's exception type, kept only if it reads as a code identifier (A47)."""
    return value if _EXCEPTION_TYPE.fullmatch(value) else None


class EvidenceReader:
    """The evidence job's statements for one project, bound by the reader it wraps."""

    def __init__(self, reader: TenantScopedReader) -> None:
        self._reader = reader

    def edges(
        self, *, callers: Sequence[str], start: datetime, end: datetime, by_caller: bool = False
    ) -> Edges:
        """Calls from `callers` in [start, end) and the equal stretch before. Server-door
        edges where there are any; otherwise the browser's, marked so (FR12)."""
        length = max(end - start, timedelta(minutes=1))
        rows = self._reader._query(
            PROJECT_EDGES_SQL if by_caller else EDGES_SQL,
            {
                "callers": list(callers),
                "before_from": _minute(start - length),
                "during_from": _minute(start),
                "during_to": _minute(end) + timedelta(minutes=1),
            },
            settings=EVIDENCE_READ_SETTINGS,
        )
        server = [row for row in rows if row["door"] == _SERVER_DOOR]
        chosen = server or [row for row in rows if row["door"] == _BROWSER_DOOR]
        truncated = len(chosen) > MAX_EDGES_READ
        facts = [
            (str(row.get("caller", "")), _edge(row, from_browser=not server))
            for row in chosen[:MAX_EDGES_READ]
        ]
        return Edges(
            edges=tuple(edge for _caller, edge in facts),
            by_caller=tuple(facts) if by_caller else (),
            truncated=truncated,
        )

    def traffic(
        self, *, service: str, lead_from: datetime, lead_to: datetime, baseline: timedelta
    ) -> Traffic:
        rows = self._reader._query(
            TRAFFIC_SQL,
            {
                "service": service,
                "before_from": _minute(lead_from - baseline),
                "lead_from": _minute(lead_from),
                "lead_to": _minute(lead_to),
            },
            settings=EVIDENCE_READ_SETTINGS,
        )
        row = rows[0] if rows else {}
        if not row.get("before_rows"):
            # No traffic recorded in the hour before: nothing to compare against.
            return Traffic(before_per_minute=None, lead_per_minute=None)
        return Traffic(
            before_per_minute=float(row["before_requests"]) / _minutes(baseline),
            lead_per_minute=float(row["lead_requests"]) / _minutes(lead_to - lead_from),
        )

    def new_error_groups(self, *, window_from: datetime, window_to: datetime) -> list[ErrorGroup]:
        """Groups received in [window_from, window_to) and in none of the hour before."""
        rows = self._reader._query(
            NEW_ERROR_GROUPS_SQL,
            {
                "look_back_from": window_from - NEW_ERROR_LOOK_BACK,
                "window_from": window_from,
                "window_to": window_to,
            },
            settings=EVIDENCE_READ_SETTINGS,
        )
        return [
            ErrorGroup(
                group_id=str(row["group_id"]),
                exception_type=exception_type_shown(str(row["exception_type"])),
                services=tuple(sorted(str(name) for name in row["services"])),
                count=int(row["occurrences"]),
                first_seen=_utc(row["first_seen"]),
            )
            for row in rows
        ]

    def hosts(self, *, start: datetime, end: datetime) -> Hosts:
        rows = self._reader._query(
            HOSTS_SQL, {"from_time": start, "to_time": end}, settings=EVIDENCE_READ_SETTINGS
        )
        named = [row for row in rows if row["host"]]
        return Hosts(
            readings=tuple(
                HostReading(
                    host=str(row["host"]),
                    measure=row["measure"],
                    utilization=float(row["utilization"]),
                    at=_utc(row["at"]),
                )
                for row in named
            ),
            # With no reading naming its host, FR52's fact is a stated absence.
            names_present=bool(named),
        )


def groups_for(groups: Sequence[ErrorGroup], services: Sequence[str]) -> list[ErrorGroup]:
    """The groups in these services, at most the bound per service (design, *Reads*)."""
    kept: list[ErrorGroup] = []
    per_service: dict[str, int] = {}
    for group in groups:
        mine = [service for service in group.services if service in services]
        if not mine or all(per_service.get(s, 0) >= MAX_ERROR_GROUPS_PER_SERVICE for s in mine):
            continue
        for service in mine:
            per_service[service] = per_service.get(service, 0) + 1
        kept.append(group)
    return kept


class EvidenceStore:
    """The telemetry store, handed out one project at a time."""

    def __init__(self, telemetry: TelemetryStore) -> None:
        self._telemetry = telemetry

    def for_project(self, org_id: object, project_id: object) -> EvidenceReader:
        return EvidenceReader(self._telemetry.for_project(org_id, project_id))


def connect_store(dsn: str) -> EvidenceStore:
    """The evidence reads through the reader account at `dsn` (`CLICKHOUSE_DSN`)."""
    return EvidenceStore(connect(dsn))


def _edge(row: dict[str, Any], *, from_browser: bool) -> EdgeFact:
    return EdgeFact(
        callee=str(row["callee"]),
        calls_before=int(row["calls_before"]),
        calls_during=int(row["calls_during"]),
        failures_before=int(row["errors_before"]),
        failures_during=int(row["errors_during"]),
        p95_before=_number(row["p95_before"]),
        p95_during=_number(row["p95_during"]),
        callee_attr=str(row["callee_attr"]),
        callee_kind=str(row["callee_kind"]),
        from_browser=from_browser,
    )


def _number(value: Any) -> float | None:
    """A merged quantile over no rows is NaN: a missing reading, never a zero (BR15)."""
    if value is None:
        return None
    number = float(value)
    return None if number != number else number


def _minutes(length: timedelta) -> float:
    return max(length / timedelta(minutes=1), 1.0)


def _minute(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(second=0, microsecond=0)


def _utc(value: Any) -> datetime:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)

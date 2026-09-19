"""The scheduler's only way into the telemetry store (ADR-012).

**Every statement here is a module constant that binds the tenant**, and the
reader refuses one that does not — the same check the API's repository makes,
from the same place (`nexovue_platform.tenant_statements`). The structural test
(`apps/api/tests/test_tenant_chokepoint_structure.py`) walks this service's source
as well as the API's, and allows the raw driver in this module alone.

The arithmetic is the Services and Errors screens' own (`nexovue_platform.readings`),
so an alert and the screen it links to cannot disagree about a p95, an error rate
or whether an error group is new.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final
from urllib.parse import unquote, urlsplit

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from nexovue_platform import readings
from nexovue_platform.tenant_statements import (
    READ_SETTINGS,
    TenantFilterMissingError,
    binds_tenant,
)

# Only this module's names may be spliced into its statements.
_QUANTILES: Final = readings.QUANTILE_ARGUMENTS
_P95: Final = readings.P95_INDEX
_SDK_PATH: Final = readings.SDK_FINGERPRINT_PATH
_MESSAGE_TEMPLATE: Final = readings.MESSAGE_TEMPLATE_SQL
_GROUP_ID: Final = readings.GROUP_ID_SQL

# The most groups one read returns, oldest first received. A read that hits it is
# continued next minute from the last group's receive time (`new_errors`).
MOST_GROUPS: Final = 50_000

# One row per service over a window of the per-minute rollup: the Services
# screen's own reading, with no span-kind filter, as that screen shows by default.
SERVICE_READINGS_SQL: Final = f"""
SELECT
    service_name,
    sum(request_count) AS request_count,
    sum(error_count) AS error_count,
    quantilesMerge({_QUANTILES})(latency_quantiles)[{_P95}] AS p95_ms
FROM endpoint_stats_1m
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND minute >= {{from_time:DateTime}}
  AND minute < {{to_time:DateTime}}
GROUP BY service_name
"""

# Every error group received in a window, with the services it happened in. The
# group id is the Errors screen's, computed from the same fragment.
ERROR_GROUPS_SQL: Final = f"""
SELECT
    group_id,
    any(exception_type) AS exception_type,
    groupUniqArray(20)(service_name) AS services,
    min(occurred) AS first_seen,
    min(received) AS first_received
FROM (
    SELECT {_GROUP_ID} AS group_id, exception_type, service_name, occurred, received
    FROM (
        SELECT exception_type, service_name, sdk_fingerprint, occurred, received,
               {_MESSAGE_TEMPLATE} AS message_template
        FROM (
            SELECT exception_type, exception_message,
                   ifNull(span_attrs.{_SDK_PATH}.:String, '') AS sdk_fingerprint,
                   service_name, timestamp AS occurred, received_at AS received
            FROM spans
            WHERE tenant_id = {{tenant_id:String}}
              AND project_id = {{project_id:String}}
              AND exception_type != ''
              AND received_at >= {{received_from:DateTime64(9)}}
              AND received_at < {{received_to:DateTime64(9)}}
            UNION ALL
            SELECT exception_type, exception_message,
                   ifNull(log_attrs.{_SDK_PATH}.:String, '') AS sdk_fingerprint,
                   service_name, timestamp AS occurred, received_at AS received
            FROM logs
            WHERE tenant_id = {{tenant_id:String}}
              AND project_id = {{project_id:String}}
              AND exception_type != ''
              AND received_at >= {{received_from:DateTime64(9)}}
              AND received_at < {{received_to:DateTime64(9)}}
        )
    )
)
GROUP BY group_id
ORDER BY first_received, group_id
LIMIT {MOST_GROUPS}
"""


class TelemetryStore:
    """The client, held here and nowhere else. What leaves this module is a reader
    bound to one tenant and project, so no evaluation can hold the raw driver."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def for_project(self, org_id: object, project_id: object) -> TenantScopedReader:
        return TenantScopedReader(self._client, tenant_id=str(org_id), project_id=str(project_id))


def connect(dsn: str) -> TelemetryStore:
    """The store at `clickhouse://user:password@host:port/database`."""
    parts = urlsplit(dsn)
    client = clickhouse_connect.get_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=unquote(parts.username or ""),
        password=unquote(parts.password or ""),
        database=parts.path.lstrip("/") or "default",
        secure=parts.scheme in ("https", "clickhouses"),
        settings=READ_SETTINGS,
    )
    return TelemetryStore(client)


class TenantScopedReader:
    """Reads telemetry for exactly one tenant and project. Built per evaluation,
    from the rule being evaluated — never shared across tenants."""

    def __init__(self, client: Client, *, tenant_id: str, project_id: str) -> None:
        self._client = client
        self._tenant_id = tenant_id
        self._project_id = project_id

    def service_readings(self, *, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self._query(SERVICE_READINGS_SQL, {"from_time": start, "to_time": end})

    def error_groups(
        self, *, received_from: datetime, received_to: datetime
    ) -> list[dict[str, Any]]:
        return self._query(
            ERROR_GROUPS_SQL, {"received_from": received_from, "received_to": received_to}
        )

    def _query(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        if not binds_tenant(sql):
            raise TenantFilterMissingError(sql)
        bound = parameters | {"tenant_id": self._tenant_id, "project_id": self._project_id}
        result = self._client.query(sql, parameters=bound, settings=READ_SETTINGS)
        return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]

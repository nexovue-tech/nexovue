"""The scheduler's two telemetry statements against real ClickHouse (ADR-012).

The evaluators are proved with readings the tests choose; this proves the
statements those readings stand in for run, return the columns the evaluators
read, and compute an error group's identity exactly as the Errors screen does.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
from live import require_stack
from nexovue_platform import readings
from nexovue_platform.tenant_statements import TenantFilterMissingError

from nexovue_scheduler.alerts.telemetry import ERROR_GROUPS_SQL, connect

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)


def _reachable() -> bool:
    parts = urlsplit(CLICKHOUSE_DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 8123), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/scheduler/tests/test_alert_telemetry_live.py",
    remedy="ClickHouse is not reachable — run `just up`",
)


def _busiest(table: str, time_column: str, where: str = "1") -> tuple[str, str]:
    """A tenant and project with rows in the last day — the dev stack's own traffic."""
    parts = urlsplit(CLICKHOUSE_DSN)
    client = clickhouse_connect.get_client(
        host=parts.hostname,
        port=parts.port,
        username=parts.username or "",
        password=parts.password or "",
        database=parts.path.lstrip("/"),
    )
    found = client.query(
        f"SELECT tenant_id, project_id FROM {table} "
        f"WHERE {time_column} >= now() - INTERVAL 1 DAY AND {where} "
        "GROUP BY tenant_id, project_id "
        "ORDER BY count() DESC LIMIT 1"
    ).result_rows
    if not found:
        pytest.fail(
            f"no {table} rows in the last day to read — send some traffic with "
            "`just otel-demo up` or `just load`"
        )
    return str(found[0][0]), str(found[0][1])


def test_service_readings_come_back_in_the_evaluators_shape() -> None:
    tenant, project = _busiest("endpoint_stats_1m", "minute")
    reader = connect(CLICKHOUSE_DSN).for_project(tenant, project)
    now = datetime.now(UTC)

    rows = reader.service_readings(start=now - timedelta(days=1), end=now)

    assert rows, "the busiest project has no services in the rollup"
    for row in rows:
        assert set(row) == {"service_name", "request_count", "error_count", "p95_ms"}
        assert row["request_count"] >= row["error_count"] >= 0
        assert row["p95_ms"] >= 0


def test_error_groups_come_back_with_the_errors_screens_identity() -> None:
    tenant, project = _busiest("spans", "received_at", "exception_type != ''")
    reader = connect(CLICKHOUSE_DSN).for_project(tenant, project)
    now = datetime.now(UTC)

    rows = reader.error_groups(received_from=now - timedelta(days=1), received_to=now)

    assert rows, "the project with exceptions returned no groups"
    for row in rows:
        assert set(row) == {
            "group_id",
            "exception_type",
            "services",
            "first_seen",
            "first_received",
        }
        assert len(row["group_id"]) == 16  # sipHash64 as lower hex
    # The statement is built from the Errors screen's own fragment, not a copy.
    assert readings.GROUP_ID_SQL in ERROR_GROUPS_SQL


def test_a_statement_without_the_tenant_is_refused_before_it_is_sent() -> None:
    reader = connect(CLICKHOUSE_DSN).for_project("t", "p")
    with pytest.raises(TenantFilterMissingError):
        reader._query("SELECT count() FROM spans", {})


def test_another_tenants_rows_are_not_read() -> None:
    """The busiest project's id, with a tenant that is not its own: nothing."""
    _, project = _busiest("endpoint_stats_1m", "minute")
    now = datetime.now(UTC)
    reader = connect(CLICKHOUSE_DSN).for_project("00000000-0000-0000-0000-000000000000", project)
    assert reader.service_readings(start=now - timedelta(days=1), end=now) == []

"""Infrastructure reads against real ClickHouse.

The offline suite proves the statements carry a tenant predicate and the
roll-ups read the semantic conventions correctly. This proves ClickHouse agrees:
that the statements parse, that the JSON paths into `resource_attrs` and `attrs`
resolve, that the parameter types bind, and that the numbers come back out the
way the roll-up expects.

**Why this suite writes rows.** No customer has ever sent a `system.*` metric to
this database — a distinct on `metric_name` across all five metric tables on
2026-08-30 returned only synthetic-check, demo-app and our own derived counters.
So there is nothing here to read, and a suite that only asserted "empty" would
prove the statement runs and nothing about what it returns. It therefore writes
a small fleet under a tenant id no organization has, reads it back through the
real query, and removes it again.

What it writes it removes: a mutation scoped to that tenant id, which is the
first column of the sort key, so ClickHouse skips every part that cannot hold
it. The rows also carry `retention_days = 1`, so the table's TTL removes them
within a day even if the mutation never runs.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import SplitResult, urlsplit

import clickhouse_connect
import pytest
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantFilterMissingError, TenantScopedRepository
from nexovue_api.infrastructure import conventions, service

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)

PROJECT: Final = "live-infrastructure-project"
HOST: Final = "live-probe-host"
CONTAINER: Final = "live-probe-container"

METRIC_TABLES: Final = ("metrics_gauge", "metrics_sum")

# One day of retention, so the table's TTL removes anything the mutation misses.
PROBE_RETENTION_DAYS: Final = 1

# How far apart the fleet's two observations sit, which is what a rate divides by.
WINDOW_SECONDS: Final = 100


def _dsn() -> SplitResult:
    return urlsplit(CLICKHOUSE_DSN)


def _reachable() -> bool:
    parts = _dsn()
    try:
        with socket.create_connection((parts.hostname or "", parts.port or 8123), timeout=2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="the live infrastructure suite",
    remedy="ClickHouse is not running — start it with `just dev`",
)


@pytest.fixture
async def clickhouse() -> AsyncIterator[Any]:
    parts = _dsn()
    client = await clickhouse_connect.get_async_client(
        host=parts.hostname,
        port=parts.port,
        username=parts.username or "",
        password=parts.password or "",
        database=parts.path.lstrip("/") or "nexovue",
    )
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
async def fleet(clickhouse: Any) -> AsyncIterator[str]:
    """A host and a container reporting under a tenant id nobody owns."""
    tenant_id = str(uuid.uuid4())
    end = datetime.now(UTC).replace(microsecond=0)
    start = end - timedelta(seconds=WINDOW_SECONDS)

    await clickhouse.command(_insert("metrics_gauge", _gauge_rows(tenant_id, start, end)))
    await clickhouse.command(_insert("metrics_sum", _sum_rows(tenant_id, start, end)))
    try:
        yield tenant_id
    finally:
        for table in METRIC_TABLES:
            await clickhouse.command(
                f"ALTER TABLE {table} DELETE WHERE tenant_id = {_quoted(tenant_id)}",
                settings={"mutations_sync": 2},
            )


def _window() -> tuple[datetime, datetime]:
    """Wide enough to hold the fleet whatever the clock did while it was written."""
    end = datetime.now(UTC) + timedelta(minutes=1)
    return end - timedelta(minutes=10), end


def _repository(client: Any, tenant_id: str) -> TenantScopedRepository:
    return TenantScopedRepository(client, tenant_id=tenant_id)


# ------------------------------------------------------------------- the reads


async def test_a_reporting_host_and_container_come_back_rolled_up(
    clickhouse: Any, fleet: str
) -> None:
    """The whole path on real ClickHouse: JSON paths, parameter binding, roll-up."""
    start, end = _window()

    answer = await service.list_nodes(
        _repository(clickhouse, fleet), project_id=PROJECT, from_time=start, to_time=end
    )
    by_kind = {node.kind: node for node in answer.nodes}

    assert set(by_kind) == {"host", "container"}, answer.nodes
    assert answer.truncated is False

    host = by_kind["host"]
    assert host.host == HOST
    # Two logical CPUs, 30% and 50% busy.
    assert host.cpu_utilization is not None
    assert round(host.cpu_utilization, 6) == 0.4
    assert host.memory_used_bytes == 6_000_000_000
    assert host.memory_limit_bytes == 10_000_000_000
    assert host.memory_utilization == 0.6
    assert host.filesystem_used_bytes == 40
    assert host.filesystem_limit_bytes == 200
    assert host.filesystem_utilization == 0.2
    # 5,000 bytes over 100 seconds on each of two directions.
    assert host.network_bytes_per_second == 100.0

    container = by_kind["container"]
    assert container.container == CONTAINER
    assert container.cpu_cores_used == 1.5
    assert container.memory_used_bytes == 500
    assert container.memory_limit_bytes == 2_000


async def test_signals_names_what_the_fleet_actually_sent(clickhouse: Any, fleet: str) -> None:
    start, end = _window()

    answer = await service.list_signals(
        _repository(clickhouse, fleet), project_id=PROJECT, from_time=start, to_time=end
    )

    assert {signal.metric_name for signal in answer.signals} == {
        conventions.SYSTEM_CPU_UTILIZATION,
        conventions.SYSTEM_MEMORY_USAGE,
        conventions.SYSTEM_FILESYSTEM_USAGE,
        conventions.SYSTEM_NETWORK_IO,
        conventions.CONTAINER_CPU_USAGE,
        conventions.CONTAINER_MEMORY_USAGE,
        conventions.CONTAINER_MEMORY_AVAILABLE,
    }
    assert all(signal.points > 0 for signal in answer.signals)
    assert all(signal.nodes == 1 for signal in answer.signals)


async def test_another_tenant_sees_none_of_that_fleet(clickhouse: Any, fleet: str) -> None:
    """Real ClickHouse, real rows, and the predicate is what hides them."""
    start, end = _window()
    stranger = str(uuid.uuid4())

    nodes = await service.list_nodes(
        _repository(clickhouse, stranger), project_id=PROJECT, from_time=start, to_time=end
    )
    signals = await service.list_signals(
        _repository(clickhouse, stranger), project_id=PROJECT, from_time=start, to_time=end
    )

    assert nodes.nodes == []
    assert signals.signals == []


async def test_a_project_with_no_host_metrics_gets_an_empty_list_not_an_error(
    clickhouse: Any,
) -> None:
    """The state every real project is in today, answered by the real database."""
    start, end = _window()

    answer = await service.list_nodes(
        _repository(clickhouse, str(uuid.uuid4())),
        project_id="a-project-that-never-existed",
        from_time=start,
        to_time=end,
    )

    assert answer.nodes == []


async def test_the_repository_refuses_a_statement_that_lost_its_tenant_predicate(
    clickhouse: Any,
) -> None:
    """The negative control against the live client: a statement without the
    predicate would read every tenant's hosts, so it never reaches the server."""
    with pytest.raises(TenantFilterMissingError):
        await _repository(clickhouse, str(uuid.uuid4())).query(
            "SELECT metric_name FROM metrics_gauge WHERE project_id = {project_id:String}",
            {"project_id": PROJECT},
        )


# ------------------------------------------------------------------- the fleet
#
# Written as SQL text rather than through the driver's row binding, because the
# JSON columns are what this suite exists to exercise and a driver that reshaped
# them would hide the thing being proved.

_COLUMNS: Final = (
    "timestamp, start_timestamp, received_at, retention_days, tenant_id, project_id, "
    "service_name, scope_name, scope_version, metric_name, metric_unit, "
    "metric_description, resource_attrs, attrs, flags, value"
)


def _quoted(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _insert(table: str, rows: list[str]) -> str:
    return f"INSERT INTO {table} ({_COLUMNS}) VALUES\n" + ",\n".join(rows)


def _row(
    tenant_id: str,
    *,
    at: datetime,
    resource: dict[str, Any],
    metric_name: str,
    unit: str,
    attributes: dict[str, Any],
    value: float,
) -> str:
    stamp = _quoted(at.strftime("%Y-%m-%d %H:%M:%S"))
    fields = [
        stamp,
        stamp,
        stamp,
        str(PROBE_RETENTION_DAYS),
        _quoted(tenant_id),
        _quoted(PROJECT),
        _quoted("otelcol"),
        _quoted(""),
        _quoted(""),
        _quoted(metric_name),
        _quoted(unit),
        _quoted(""),
        _quoted(json.dumps(resource)),
        _quoted(json.dumps(attributes)),
        "0",
        repr(float(value)),
    ]
    return "(" + ", ".join(fields) + ")"


_HOST_RESOURCE: Final = {"host": {"name": HOST}}
_CONTAINER_RESOURCE: Final = {"host": {"name": HOST}, "container": {"name": CONTAINER}}


def _gauge_rows(tenant_id: str, start: datetime, end: datetime) -> list[str]:
    """`system.cpu.utilization` and `container.cpu.usage` are Gauges."""
    rows = []
    for at in (start, end):
        for logical_number, busy in (("0", 0.3), ("1", 0.5)):
            for mode, fraction in (("idle", 1.0 - busy), ("user", busy)):
                rows.append(
                    _row(
                        tenant_id,
                        at=at,
                        resource=_HOST_RESOURCE,
                        metric_name=conventions.SYSTEM_CPU_UTILIZATION,
                        unit="1",
                        attributes={"cpu": {"mode": mode, "logical_number": logical_number}},
                        value=fraction,
                    )
                )
        rows.append(
            _row(
                tenant_id,
                at=at,
                resource=_CONTAINER_RESOURCE,
                metric_name=conventions.CONTAINER_CPU_USAGE,
                unit="{cpu}",
                attributes={},
                value=1.5,
            )
        )
    return rows


def _sum_rows(tenant_id: str, start: datetime, end: datetime) -> list[str]:
    """Memory, filesystem and network are UpDownCounters and Counters."""
    rows = []
    for at in (start, end):
        for state, level in (("used", 6e9), ("free", 2e9), ("cached", 2e9)):
            rows.append(
                _row(
                    tenant_id,
                    at=at,
                    resource=_HOST_RESOURCE,
                    metric_name=conventions.SYSTEM_MEMORY_USAGE,
                    unit="By",
                    attributes={"system": {"memory": {"state": state}}},
                    value=level,
                )
            )
        for mountpoint, used, free in (("/", 30, 70), ("/data", 10, 90)):
            for state, level in (("used", used), ("free", free)):
                rows.append(
                    _row(
                        tenant_id,
                        at=at,
                        resource=_HOST_RESOURCE,
                        metric_name=conventions.SYSTEM_FILESYSTEM_USAGE,
                        unit="By",
                        attributes={
                            "system": {"filesystem": {"state": state, "mountpoint": mountpoint}}
                        },
                        value=level,
                    )
                )
        for metric_name, level in (
            (conventions.CONTAINER_MEMORY_USAGE, 500),
            (conventions.CONTAINER_MEMORY_AVAILABLE, 1_500),
        ):
            rows.append(
                _row(
                    tenant_id,
                    at=at,
                    resource=_CONTAINER_RESOURCE,
                    metric_name=metric_name,
                    unit="By",
                    attributes={},
                    value=level,
                )
            )

    # A counter only rises: 5,000 bytes across the window on each direction.
    for direction in ("receive", "transmit"):
        for at, level in ((start, 0), (end, 5_000)):
            rows.append(
                _row(
                    tenant_id,
                    at=at,
                    resource=_HOST_RESOURCE,
                    metric_name=conventions.SYSTEM_NETWORK_IO,
                    unit="By",
                    attributes={"network": {"io": {"direction": direction}}},
                    value=level,
                )
            )
    return rows

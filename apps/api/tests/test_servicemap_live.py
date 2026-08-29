"""Service map reads against the running stack.

The offline suite proves the code cannot construct a cross-tenant read. This one
proves the statement is one ClickHouse accepts and answers: the merge function,
the parameter types and the column names are all things a fake store cannot
check.

It fails when the stack is not up, because a skip here is indistinguishable from
a pass — `live.require_stack` is that rule, and `NEXOVUE_SKIP_LIVE_TESTS=1` is
the deliberate way out.

It reads only. Nothing is written, so nothing is cleaned up.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest_asyncio
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.servicemap import service
from nexovue_api.servicemap.schemas import EDGE_CALL, EDGE_QUEUE

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)

REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

TENANT_THAT_NEVER_EXISTED: Final = "00000000-0000-0000-0000-000000000000"

# The rollup buckets by minute, so the window has to include the last bucket.
LAST_BUCKET_SLACK: Final = timedelta(minutes=1)

EDGE_KINDS_THE_PIPELINE_WRITES: Final = frozenset({EDGE_CALL, EDGE_QUEUE})


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or 8123), REACHABILITY_TIMEOUT_SECONDS
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN),
    suite="the live service map suite",
    remedy="ClickHouse is not running — start the dev stack with `just dev`",
)


@dataclass(frozen=True, slots=True)
class BusiestProject:
    """The tenant and project with the most edges, and the window holding them."""

    tenant_id: str
    project_id: str
    window_start: datetime
    window_end: datetime


@pytest_asyncio.fixture
async def clickhouse() -> AsyncIterator[AsyncClient]:
    parts = urlsplit(CLICKHOUSE_DSN)
    client = await clickhouse_connect.get_async_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "",
        password=parts.password or "",
        database=(parts.path or "/").lstrip("/"),
    )
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture
async def busiest(clickhouse: AsyncClient) -> BusiestProject:
    result = await clickhouse.query(
        "SELECT tenant_id, project_id, min(minute) AS first_minute, max(minute) AS last_minute "
        "FROM service_edges_1m GROUP BY tenant_id, project_id "
        "ORDER BY sum(call_count) DESC LIMIT 1"
    )
    assert result.result_rows, (
        "service_edges_1m is empty — this suite would prove nothing. "
        "Run the load generator, or check that the trace pipeline is writing edges."
    )

    tenant_id, project_id, first_minute, last_minute = result.result_rows[0]
    return BusiestProject(
        tenant_id=str(tenant_id),
        project_id=str(project_id),
        window_start=first_minute,
        window_end=last_minute + LAST_BUCKET_SLACK,
    )


def _repository(client: AsyncClient, tenant_id: str) -> TenantScopedRepository:
    return TenantScopedRepository(client, tenant_id=tenant_id)


# ----------------------------------------------------------- the query answers


async def test_the_map_comes_back_with_edges_and_readable_latencies(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """Proves the merge function and parameter types are ones the server accepts."""
    graph = await service.get_service_map(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.MAX_LIMIT,
    )

    assert graph.edges, "the busiest project returned no dependency edges"
    for edge in graph.edges:
        assert edge.caller and edge.callee
        assert edge.edge_kind in EDGE_KINDS_THE_PIPELINE_WRITES
        assert edge.call_count > 0
        assert edge.calls_per_minute > 0
        assert 0.0 <= edge.error_rate <= 1.0
        # A merged state that was read as a raw blob would not be ordered.
        assert edge.p50_ms <= edge.p95_ms <= edge.p99_ms


async def test_the_busiest_edge_is_first_so_a_limit_keeps_the_backbone(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    graph = await service.get_service_map(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.MAX_LIMIT,
    )

    volumes = [edge.call_count for edge in graph.edges]
    assert volumes == sorted(volumes, reverse=True)


async def test_every_node_the_graph_lists_is_reached_by_an_edge_it_carries(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    graph = await service.get_service_map(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.MAX_LIMIT,
    )

    named_by_edges = {name for edge in graph.edges for name in (edge.caller, edge.callee)}
    assert set(graph.services) == named_by_edges
    assert graph.services == sorted(graph.services)


async def test_the_call_totals_are_the_totals_the_table_holds_for_this_tenant(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """Counted independently of the query under test, so an over-broad read shows."""
    expected = await clickhouse.query(
        "SELECT sum(call_count) FROM service_edges_1m "
        "WHERE tenant_id = {tenant_id:String} AND project_id = {project_id:String} "
        "AND minute >= {from_time:DateTime} AND minute < {to_time:DateTime}",
        parameters={
            "tenant_id": busiest.tenant_id,
            "project_id": busiest.project_id,
            "from_time": busiest.window_start,
            "to_time": busiest.window_end,
        },
    )
    total_in_table = int(expected.result_rows[0][0])

    graph = await service.get_service_map(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.MAX_LIMIT,
    )

    assert total_in_table > 0
    assert sum(edge.call_count for edge in graph.edges) == total_in_table


# ------------------------------------------------------------------- isolation


async def test_another_tenants_project_reads_as_an_empty_graph_against_real_data(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """The same project id, a different session tenant, and nothing comes back."""
    graph = await service.get_service_map(
        _repository(clickhouse, TENANT_THAT_NEVER_EXISTED),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.MAX_LIMIT,
    )

    assert graph.edges == []
    assert graph.services == []

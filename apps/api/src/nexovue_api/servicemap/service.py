"""Turning edge rows into the dependency graph the API promises.

No SQL text and no HTTP. The rates and the quantile unpacking come from
`rollup`, which both rollup tables share; the only work of this module
is deriving the node list from the edges, so the two can never disagree.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from nexovue_api import rollup
from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.servicemap import queries
from nexovue_api.servicemap.schemas import ServiceEdge, ServiceMapResponse

# A graph a person can read, and a bound on the response. A system with more
# distinct edges than this in one window is one nobody renders whole anyway.
DEFAULT_LIMIT: Final = 200
MAX_LIMIT: Final = 1_000


async def get_service_map(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    limit: int,
) -> ServiceMapResponse:
    """The window's dependency graph, busiest edges first."""
    rows = await repository.query(
        queries.SERVICE_EDGES_SQL,
        {
            "project_id": project_id,
            "from_time": from_time,
            "to_time": to_time,
            "limit": limit,
        },
    )
    minutes = rollup.window_minutes(from_time, to_time)
    edges = [_to_edge(row, minutes) for row in rows]
    return ServiceMapResponse(
        window_start=from_time,
        window_end=to_time,
        services=_nodes(edges),
        edges=edges,
    )


def _nodes(edges: list[ServiceEdge]) -> list[str]:
    """Every service an edge names, once, sorted so the graph renders stably."""
    return sorted({name for edge in edges for name in (edge.caller, edge.callee)})


def _to_edge(row: dict[str, Any], minutes: float) -> ServiceEdge:
    calls = int(row["call_count"])
    errors = int(row["error_count"])
    latency = rollup.Latency(row["latency_ms"])
    return ServiceEdge(
        caller=values.text(row["client_service"]),
        callee=values.text(row["server_service"]),
        edge_kind=values.text(row["edge_kind"]),
        call_count=calls,
        error_count=errors,
        calls_per_minute=rollup.per_minute(calls, minutes),
        error_rate=rollup.failure_ratio(errors, calls),
        p50_ms=latency.p50_ms,
        p95_ms=latency.p95_ms,
        p99_ms=latency.p99_ms,
    )

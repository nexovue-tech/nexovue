"""Turning poller readings into the shapes the API promises.

No SQL text and no HTTP.

**Nothing here has run against a real Postgres.** Door 4b's poller has never
been pointed at a live database, so `metrics_gauge` holds no row under these
metric names and every response below is the empty one. That is deliberate: an
empty window is an empty list, never an error and never a sample row. The shapes
follow the writer in `nexovue_scheduler.pollers.envelope`, which is the only
source of truth available until a database is connected.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Final

from nexovue_api import rollup
from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.dbstats import queries
from nexovue_api.dbstats.schemas import (
    ConnectionHealthResponse,
    ConnectionStateCount,
    DatabaseServer,
    SlowStatementsResponse,
    StatementCost,
    StatementTrend,
    StatementTrendPoint,
    StatementTrendResponse,
)
from nexovue_api.window import receive_window

MILLISECONDS_PER_SECOND: Final = 1000.0

# The poller already sends only its hundred costliest statements per database
# (`postgres_stats.TOP_STATEMENTS_LIMIT`), so this binds when several databases
# are connected rather than truncating one.
DEFAULT_STATEMENT_LIMIT: Final = 50
MAX_STATEMENT_LIMIT: Final = 500

# Postgres has a handful of backend states, times however many servers a
# customer connects. Generous, and still a bound.
MAX_CONNECTION_ROWS: Final = 500

# One row per statement per bucket. Sized so a project at the statement cap still gets a
# full window of points rather than a prefix of them.
MAX_TREND_ROWS: Final = MAX_STATEMENT_LIMIT * 200


async def slow_statements(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    limit: int,
    server: str | None = None,
    min_mean_ms: float | None = None,
) -> SlowStatementsResponse:
    """The costliest normalized statements in the window, slowest first."""
    parameters = _window_parameters(project_id, from_time, to_time) | {"limit": limit}
    if server is not None:
        parameters["server"] = server
    # Asked in milliseconds because that is what the column is labelled with;
    # stored in seconds, so it is converted here rather than in SQL.
    if min_mean_ms is not None:
        parameters["min_mean_seconds"] = min_mean_ms / MILLISECONDS_PER_SECOND

    rows = await repository.query(
        queries.slow_statements_sql(server=server is not None, min_mean=min_mean_ms is not None),
        parameters,
    )
    return SlowStatementsResponse(statements=[_to_statement(row) for row in rows])


async def statement_trends(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
) -> StatementTrendResponse:
    """Each statement's cost across the window, for the sparkline in its row."""
    bucket_seconds = rollup.trend_bucket_seconds(from_time, to_time)
    rows = await repository.query(
        queries.STATEMENT_TRENDS_SQL,
        _window_parameters(project_id, from_time, to_time)
        | {"bucket_seconds": bucket_seconds, "limit": MAX_TREND_ROWS},
    )

    by_statement: dict[tuple[str, str, int], list[StatementTrendPoint]] = defaultdict(list)
    for row in rows:
        key = (
            values.text(row["query_hash"]),
            values.text(row["server_address"]),
            int(row["server_port"]),
        )
        by_statement[key].append(
            StatementTrendPoint(
                bucket=values.nanos_to_datetime(row["bucket_nanos"]),
                mean_ms=_milliseconds(row["mean_seconds"]),
            )
        )

    return StatementTrendResponse(
        window_start=from_time,
        window_end=to_time,
        bucket_seconds=bucket_seconds,
        statements=[
            StatementTrend(
                query_hash=query_hash,
                server=DatabaseServer(address=address, port=port),
                points=points,
            )
            for (query_hash, address, port), points in sorted(by_statement.items())
        ],
    )


async def connection_health(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
) -> ConnectionHealthResponse:
    """Backends by state, and what they add up to right now."""
    rows = await repository.query(
        queries.CONNECTION_HEALTH_SQL,
        _window_parameters(project_id, from_time, to_time) | {"limit": MAX_CONNECTION_ROWS},
    )
    states = [_to_connection_state(row) for row in rows]
    return ConnectionHealthResponse(
        states=states,
        latest_backends_total=sum(state.latest_backends for state in states),
    )


def _window_parameters(project_id: str, from_time: datetime, to_time: datetime) -> dict[str, Any]:
    """Both clocks. `received_at` is what prunes partitions; `timestamp` is what
    the caller asked about."""
    received_from, received_to = receive_window(from_time, to_time)
    return {
        "project_id": project_id,
        "from_time": from_time,
        "to_time": to_time,
        "received_from": received_from,
        "received_to": received_to,
    }


def _to_statement(row: dict[str, Any]) -> StatementCost:
    return StatementCost(
        query_hash=values.text(row["query_hash"]),
        namespace=values.text(row["namespace"]),
        server=_to_server(row),
        db_system=values.text(row["db_system"]),
        latest_mean_ms=_milliseconds(row["latest_mean_seconds"]),
        worst_mean_ms=_milliseconds(row["worst_mean_seconds"]),
        calls_total=int(row["calls_total"]),
        calls_in_window=int(row["calls_in_window"]),
        readings=int(row["readings"]),
        latest_reading_at=values.nanos_to_datetime(row["latest_nanos"]),
    )


def _to_connection_state(row: dict[str, Any]) -> ConnectionStateCount:
    return ConnectionStateCount(
        state=values.text(row["state"]),
        server=_to_server(row),
        # The poller writes this as an integer point; the column is Float64.
        latest_backends=int(row["latest_backends"]),
        peak_backends=int(row["peak_backends"]),
        readings=int(row["readings"]),
        latest_reading_at=values.nanos_to_datetime(row["latest_nanos"]),
    )


def _to_server(row: dict[str, Any]) -> DatabaseServer:
    return DatabaseServer(address=values.text(row["server_address"]), port=int(row["server_port"]))


def _milliseconds(seconds: Any) -> float:
    """Seconds in the store, milliseconds on the wire — converted in one place."""
    return float(seconds) * MILLISECONDS_PER_SECOND

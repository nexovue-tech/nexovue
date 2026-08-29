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

from datetime import datetime
from typing import Any, Final

from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.dbstats import queries
from nexovue_api.dbstats.schemas import (
    ConnectionHealthResponse,
    ConnectionStateCount,
    DatabaseServer,
    SlowStatementsResponse,
    StatementCost,
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


async def slow_statements(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    limit: int,
) -> SlowStatementsResponse:
    """The costliest normalized statements in the window, slowest first."""
    rows = await repository.query(
        queries.SLOW_STATEMENTS_SQL,
        _window_parameters(project_id, from_time, to_time) | {"limit": limit},
    )
    return SlowStatementsResponse(statements=[_to_statement(row) for row in rows])


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
    the caller asked about (ADR 0067)."""
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

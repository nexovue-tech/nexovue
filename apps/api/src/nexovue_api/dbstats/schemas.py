"""What the database-statistics endpoints put on the wire.

Durations are milliseconds here and seconds in the store; the conversion happens
once, in the service.
"""

from __future__ import annotations

from pydantic import BaseModel

from nexovue_api.wire import IsoTimestamp


class DatabaseServer(BaseModel):
    """Which server a reading came from. Address and port, never a DSN."""

    address: str
    port: int


class StatementCost(BaseModel):
    """One normalized statement's cost, as `pg_stat_statements` reported it.

    There is no statement text on purpose. `pg_stat_statements` does not always
    strip literals — a query built by concatenation carries its parameters into
    `query` — so the poller sends `queryid` and nothing else. A person matches
    the hash back against their own `pg_stat_statements` to see the text.
    """

    query_hash: str
    # The database the statement ran in, from `db.namespace`.
    namespace: str
    server: DatabaseServer
    db_system: str
    latest_mean_ms: float
    worst_mean_ms: float
    # Cumulative since the last `pg_stat_statements_reset()`.
    calls_total: int
    # The rise across the window. A reset inside the window makes this a
    # floor rather than a count, which is why the total is reported beside it.
    calls_in_window: int
    readings: int
    latest_reading_at: IsoTimestamp


class SlowStatementsResponse(BaseModel):
    """Slowest first. An empty list means the poller published nothing here."""

    statements: list[StatementCost]


class ConnectionStateCount(BaseModel):
    """How many backends were in one state on one server."""

    state: str
    server: DatabaseServer
    latest_backends: int
    peak_backends: int
    readings: int
    latest_reading_at: IsoTimestamp


class ConnectionHealthResponse(BaseModel):
    states: list[ConnectionStateCount]
    # The latest readings summed across every state and server returned.
    latest_backends_total: int

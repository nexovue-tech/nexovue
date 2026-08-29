"""Reading `pg_stat_statements` and `pg_stat_activity` out of a customer's database.

Strictly read-only, and enforced three ways rather than promised once: the
customer grants a role with `pg_monitor` and no write privilege, the session is
opened with `default_transaction_read_only`, and the only statements this module
contains are the two `SELECT`s below. A monitoring connection that can write is
a monitoring connection that will eventually write.

What lands on the graph is the shape the frozen fixture froze
(`packages/contracts/fixtures/door4b-dbstats.bin`): mean execution time per
normalized statement, and backend connections by state.

**We never send the statement text.** `pg_stat_statements` normalizes literals
out, but not always — a query built by string concatenation carries its
parameters into `query`, and those are customer data we have no business
storing. `queryid` identifies the statement without carrying it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Protocol

# Ordered by total time, because the deliverable is "what is slow here", and a
# hundred rows of that is an answer where ten thousand rows is a data dump.
TOP_STATEMENTS_LIMIT: Final = 100

MILLISECONDS_PER_SECOND: Final = 1000.0

DB_SYSTEM_POSTGRESQL: Final = "postgresql"

# `queryid` is stable across restarts but not across major versions or a
# `pg_stat_statements_reset()`; it identifies a statement, not a history.
TOP_STATEMENTS_SQL: Final = """
    SELECT statements.queryid::text AS query_hash,
           databases.datname        AS namespace,
           statements.calls         AS calls,
           statements.mean_exec_time AS mean_milliseconds
    FROM pg_stat_statements AS statements
    JOIN pg_database AS databases ON databases.oid = statements.dbid
    WHERE statements.queryid IS NOT NULL
    ORDER BY statements.total_exec_time DESC
    LIMIT $1
"""

# `state` is null for background workers, which are not client connections and
# would otherwise arrive as a nameless bucket on the graph.
CONNECTION_STATES_SQL: Final = """
    SELECT state, count(*) AS backends
    FROM pg_stat_activity
    WHERE state IS NOT NULL
    GROUP BY state
    ORDER BY state
"""


class PostgresPollError(RuntimeError):
    """The database could not be read, so there is nothing honest to publish."""


@dataclass(frozen=True, slots=True)
class StatementStats:
    """One normalized statement's cost since the last reset."""

    query_hash: str
    namespace: str
    calls: int
    mean_seconds: float


@dataclass(frozen=True, slots=True)
class ConnectionCount:
    """How many backends are in one state right now."""

    state: str
    backends: int


@dataclass(frozen=True, slots=True)
class DatabaseSnapshot:
    """Everything one poll of one database produced."""

    statements: list[StatementStats]
    connections: list[ConnectionCount]


class _Fetcher(Protocol):
    """The one method this module needs from a connection, named so the pure
    normalization below can be tested without a database."""

    async def fetch(self, query: str, *args: Any) -> list[Any]: ...


def statement_stats_from_rows(rows: list[Any]) -> list[StatementStats]:
    """Normalize `TOP_STATEMENTS_SQL` rows.

    Postgres reports milliseconds; OTLP durations are seconds. Converting at the
    boundary means no unit ambiguity survives past this function — the fixture's
    unit is `s`, and a millisecond value under an `s` label is off by a thousand
    with nothing to reveal it.
    """
    return [
        StatementStats(
            query_hash=str(row["query_hash"]),
            namespace=str(row["namespace"]),
            calls=int(row["calls"]),
            mean_seconds=float(row["mean_milliseconds"]) / MILLISECONDS_PER_SECOND,
        )
        for row in rows
    ]


def connection_counts_from_rows(rows: list[Any]) -> list[ConnectionCount]:
    """Normalize `CONNECTION_STATES_SQL` rows."""
    return [ConnectionCount(state=str(row["state"]), backends=int(row["backends"])) for row in rows]


async def collect_snapshot(
    connection: _Fetcher, *, statement_limit: int = TOP_STATEMENTS_LIMIT
) -> DatabaseSnapshot:
    """Both readings from one connection, in one poll."""
    statements = await connection.fetch(TOP_STATEMENTS_SQL, statement_limit)
    connections = await connection.fetch(CONNECTION_STATES_SQL)
    return DatabaseSnapshot(
        statements=statement_stats_from_rows(statements),
        connections=connection_counts_from_rows(connections),
    )

"""Database statistics: tenant isolation, the units, and the empty case.

**These endpoints have no real data behind them.** Door 4b's poller has never
run against a live Postgres, so `metrics_gauge` holds no row under either metric
name. The fake store here therefore does two jobs: it proves the empty response
is clean, and it proves the row mapping is right *for the shape the publisher
actually emits* — the shape is asserted against
`nexovue_scheduler.pollers.envelope` at the bottom of this file, so a rename on
either side fails here rather than showing an empty chart in production.

The fake answers from the values the repository *bound*, never from the
statement text, so a query that lost its tenant predicate finds nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest

from nexovue_api.data.clickhouse.base import TENANT_BINDING, TenantScopedRepository
from nexovue_api.dbstats import queries, service
from nexovue_api.window import RECEIVE_CLOCK_SLACK

TENANT_A: Final = "org-a"
TENANT_B: Final = "org-b"
PROJECT_A: Final = "project-a"
PROJECT_B: Final = "project-b"

_READING_NANOS: Final = 1_788_038_991_898_000_000

_STATEMENT_COLUMNS: Final = [
    "query_hash",
    "namespace",
    "server_address",
    "server_port",
    "db_system",
    "latest_mean_seconds",
    "worst_mean_seconds",
    "calls_total",
    "calls_in_window",
    "readings",
    "latest_nanos",
]
_CONNECTION_COLUMNS: Final = [
    "state",
    "server_address",
    "server_port",
    "latest_backends",
    "peak_backends",
    "readings",
    "latest_nanos",
]


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class FakePollerStore:
    """Serves poller readings, honouring the tenant and project the query bound."""

    def __init__(self, owners: dict[str, str]) -> None:
        # tenant -> the one project it owns
        self._owners = owners
        self.last_parameters: dict[str, Any] = {}

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        self.last_parameters = bound
        if self._owners.get(bound["tenant_id"]) != bound.get("project_id"):
            return FakeQueryResult([], [])

        if "GROUP BY query_hash" in sql:
            return FakeQueryResult(_STATEMENT_COLUMNS, [_statement_row(bound["tenant_id"])])
        return FakeQueryResult(_CONNECTION_COLUMNS, _connection_rows())


def _statement_row(tenant: str) -> tuple[Any, ...]:
    return (
        f"hash-for-{tenant}",
        "orders",
        "db.internal.example",
        5432,
        "postgresql",
        0.3,  # latest mean, seconds
        1.25,  # worst mean, seconds
        90_000,  # cumulative calls
        1_200,  # calls during the window
        12,
        _READING_NANOS,
    )


def _connection_rows() -> list[tuple[Any, ...]]:
    return [
        ("active", "db.internal.example", 5432, 41.0, 88.0, 12, _READING_NANOS),
        ("idle in transaction", "db.internal.example", 5432, 7.0, 19.0, 12, _READING_NANOS),
    ]


def _store() -> FakePollerStore:
    return FakePollerStore({TENANT_A: PROJECT_A, TENANT_B: PROJECT_B})


def _repository(tenant: str, store: FakePollerStore) -> TenantScopedRepository:
    return TenantScopedRepository(store, tenant_id=tenant)  # type: ignore[arg-type]


def _window() -> tuple[datetime, datetime]:
    end = datetime(2026, 8, 29, 22, 0, tzinfo=UTC)
    return end - timedelta(hours=6), end


def _every_statement() -> list[str]:
    return [
        value
        for name, value in vars(queries).items()
        if name.endswith("_SQL") and isinstance(value, str)
    ]


# ------------------------------------------------------------------- tenancy


def test_every_database_statement_carries_the_tenant_binding() -> None:
    statements = _every_statement()

    assert statements, "no statements found — this test would prove nothing"
    for sql in statements:
        assert TENANT_BINDING in sql


async def test_two_organizations_cannot_read_each_others_slow_statements() -> None:
    """A query hash is a fingerprint of somebody else's schema and traffic."""
    store = _store()
    start, end = _window()

    mine = await service.slow_statements(
        _repository(TENANT_A, store), project_id=PROJECT_A, from_time=start, to_time=end, limit=10
    )
    theirs = await service.slow_statements(
        _repository(TENANT_B, store), project_id=PROJECT_B, from_time=start, to_time=end, limit=10
    )

    assert [row.query_hash for row in mine.statements] == [f"hash-for-{TENANT_A}"]
    assert [row.query_hash for row in theirs.statements] == [f"hash-for-{TENANT_B}"]


async def test_another_tenants_project_returns_no_connection_health() -> None:
    store = _store()
    start, end = _window()

    stolen = await service.connection_health(
        _repository(TENANT_A, store), project_id=PROJECT_B, from_time=start, to_time=end
    )

    assert stolen.states == []


async def test_no_read_binds_a_tenant_it_was_not_given() -> None:
    """Guards the tests above: presence alone would pass if tenant were ignored."""
    store = _store()
    start, end = _window()

    await service.connection_health(
        _repository(TENANT_A, store), project_id=PROJECT_A, from_time=start, to_time=end
    )

    assert store.last_parameters["tenant_id"] == TENANT_A
    assert TENANT_B not in store.last_parameters.values()


async def test_a_read_bounds_both_clocks() -> None:
    """`metrics_gauge` is partitioned on `received_at`, so only that prunes."""
    store = _store()
    start, end = _window()

    await service.slow_statements(
        _repository(TENANT_A, store), project_id=PROJECT_A, from_time=start, to_time=end, limit=10
    )

    assert store.last_parameters["from_time"] == start
    assert store.last_parameters["to_time"] == end
    assert store.last_parameters["received_from"] == start - RECEIVE_CLOCK_SLACK
    assert store.last_parameters["received_to"] == end + RECEIVE_CLOCK_SLACK


# ---------------------------------------------------------------- no data yet


async def test_no_polled_database_is_an_empty_list_and_not_an_error() -> None:
    """The state this endpoint is actually in: nothing has ever polled.

    Before a customer grants the read-only role the poller needs, there is no
    row to read. That is normal, so it answers 200 with nothing in it.
    """
    store = FakePollerStore({})
    start, end = _window()

    statements = await service.slow_statements(
        _repository(TENANT_A, store), project_id=PROJECT_A, from_time=start, to_time=end, limit=10
    )
    connections = await service.connection_health(
        _repository(TENANT_A, store), project_id=PROJECT_A, from_time=start, to_time=end
    )

    assert statements.statements == []
    assert connections.states == []
    assert connections.latest_backends_total == 0


# ------------------------------------------------------ the readings, mapped


async def test_seconds_in_the_store_are_milliseconds_on_the_wire() -> None:
    """Postgres reports milliseconds, the poller converts to OTLP seconds, and
    the wire is milliseconds again. Getting this wrong is off by a thousand."""
    start, end = _window()

    result = await service.slow_statements(
        _repository(TENANT_A, _store()),
        project_id=PROJECT_A,
        from_time=start,
        to_time=end,
        limit=10,
    )

    statement = result.statements[0]
    assert statement.latest_mean_ms == pytest.approx(300.0)
    assert statement.worst_mean_ms == pytest.approx(1250.0)


async def test_a_statement_carries_its_hash_and_never_its_text() -> None:
    """`pg_stat_statements.query` can hold customer literals, so it is not sent."""
    start, end = _window()

    result = await service.slow_statements(
        _repository(TENANT_A, _store()),
        project_id=PROJECT_A,
        from_time=start,
        to_time=end,
        limit=10,
    )

    statement = result.statements[0]
    assert statement.query_hash
    assert statement.namespace == "orders"
    assert "query" not in statement.model_dump()
    assert "sql" not in statement.model_dump()


async def test_cumulative_calls_and_calls_in_the_window_are_both_reported() -> None:
    """`calls` counts since the last reset, so the total alone hides recent load."""
    start, end = _window()

    result = await service.slow_statements(
        _repository(TENANT_A, _store()),
        project_id=PROJECT_A,
        from_time=start,
        to_time=end,
        limit=10,
    )

    statement = result.statements[0]
    assert statement.calls_total == 90_000
    assert statement.calls_in_window == 1_200


async def test_connection_health_totals_the_latest_reading_of_every_state() -> None:
    """The number to compare against `max_connections`."""
    start, end = _window()

    health = await service.connection_health(
        _repository(TENANT_A, _store()), project_id=PROJECT_A, from_time=start, to_time=end
    )

    assert [state.state for state in health.states] == ["active", "idle in transaction"]
    assert [state.latest_backends for state in health.states] == [41, 7]
    assert health.latest_backends_total == 48
    assert health.states[0].server.address == "db.internal.example"
    assert health.states[0].server.port == 5432


# ---------------------------------------------- the contract with the writer


def test_the_metric_names_read_here_are_the_ones_the_poller_publishes() -> None:
    """The one assertion that can fail while no data exists to prove the rest.

    Nothing has ever been polled, so no test here can show a real row. What can
    still be held is that the names being read are the names being written.
    """
    import nexovue_scheduler.pollers.envelope as envelope

    assert queries.METRIC_OPERATION_DURATION == envelope.METRIC_OPERATION_DURATION
    assert queries.METRIC_CONNECTION_COUNT == envelope.METRIC_CONNECTION_COUNT


def test_the_attribute_paths_read_here_are_the_keys_the_poller_writes() -> None:
    """ClickHouse's JSON type nests on the dots, so the key *is* the path."""
    import nexovue_scheduler.pollers.envelope as envelope

    assert f"attrs.{envelope.ATTR_QUERY_HASH}" in queries.QUERY_HASH
    assert f"attrs.{envelope.ATTR_DB_NAMESPACE}" in queries.DB_NAMESPACE
    assert f"attrs.{envelope.ATTR_DB_SYSTEM}" in queries.DB_SYSTEM
    assert f"attrs.{envelope.ATTR_OPERATION_CALLS}" in queries.CALLS
    assert f"attrs.{envelope.ATTR_CONNECTION_STATE}" in queries.CONNECTION_STATE
    assert f"resource_attrs.{envelope.ATTR_SERVER_ADDRESS}" in queries.SERVER_ADDRESS
    assert f"resource_attrs.{envelope.ATTR_SERVER_PORT}" in queries.SERVER_PORT


# ---------------------------------------------------------------------- filters
#
# Inside the statement, ahead of its `LIMIT`: the list is capped at five hundred,
# so filtering what came back would answer "the slow ones among the five hundred
# worst" and have no way to say so.


def test_an_unfiltered_statement_list_carries_neither_predicate() -> None:
    statement = queries.slow_statements_sql(server=False, min_mean=False)

    assert "server:String" not in statement
    assert "HAVING" not in statement


async def test_one_server_narrows_the_rows_that_are_read() -> None:
    store = _store()
    start, end = _window()

    await service.slow_statements(
        _repository(TENANT_A, store),
        project_id=PROJECT_A,
        from_time=start,
        to_time=end,
        limit=10,
        server="db-primary",
    )

    assert store.last_parameters["server"] == "db-primary"


async def test_a_floor_asked_in_milliseconds_is_bound_in_seconds() -> None:
    """The column holds seconds and the screen shows milliseconds.

    Binding 250 against a column of seconds would return every statement in the
    store, which reads as "the filter does nothing" rather than as a unit bug.
    """
    store = _store()
    start, end = _window()

    await service.slow_statements(
        _repository(TENANT_A, store),
        project_id=PROJECT_A,
        from_time=start,
        to_time=end,
        limit=10,
        min_mean_ms=250.0,
    )

    assert store.last_parameters["min_mean_seconds"] == pytest.approx(0.25)


def test_the_floor_is_tested_against_the_worst_reading_not_the_latest() -> None:
    """The list is ranked by the worst reading, so the filter matches the ranking.

    A statement that spiked to two seconds and has since settled is the one the
    reader is looking for, and testing the latest reading would hide it.
    """
    assert "worst_mean_seconds >=" in queries.MIN_MEAN_CLAUSE

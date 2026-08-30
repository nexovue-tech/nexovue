"""Service map reads: the statement, the derived nodes, and tenant isolation.

The query is captured before it reaches ClickHouse and its bound parameters are
inspected. A statement that lost its `tenant_id` predicate would still return
correct-looking edges against a store holding one tenant's data — so asserting
on the statement is what proves the filter is there.

`test_servicemap_live.py` runs the same read against the running stack.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from nexovue_api import rollup
from nexovue_api.data.clickhouse.base import TenantScopedRepository, binds_tenant
from nexovue_api.servicemap import queries, schemas, service

TENANT_A: Final = "org-a"
TENANT_B: Final = "org-b"
OTHER_TENANT: Final = "org-nobody"
PROJECT: Final = "project-1"

_MIGRATIONS: Final = Path(__file__).resolve().parents[3] / "deploy" / "clickhouse" / "migrations"
# Every migration that shapes the table, in order. A column added later is still
# a column of the table, so checking only the CREATE would miss it.
SERVICE_EDGES_MIGRATIONS: Final = [
    _MIGRATIONS / "000014_service_edges_1m.up.sql",
    _MIGRATIONS / "000027_service_edges_peer_kind.up.sql",
]

WINDOW_MINUTES: Final = 60.0

EDGE_COLUMNS: Final = [
    "client_service",
    "server_service",
    "edge_kind",
    "server_peer_kind",
    "call_count",
    "error_count",
    "latency_ms",
]

# tenant -> the edges that tenant owns
_EDGES: Final[dict[str, list[tuple[Any, ...]]]] = {
    TENANT_A: [
        ("storefront", "checkout", "call", "", 1200, 60, [12.5, 61.8, 99.0]),
        ("checkout", "payments", "call", "", 600, 0, [8.0, 20.0, 30.0]),
        # A callee that never reported for itself, named from the caller's own
        # `server.address` and labelled with the technology it said it spoke.
        ("checkout", "(valkey-cart)", "call", "redis", 300, 0, [0.4, 1.1, 2.0]),
    ],
    TENANT_B: [("ledger", "ledger-worker", "queue", "", 40, 4, [1.0, 2.0, 3.0])],
}


def _window() -> tuple[datetime, datetime]:
    end = datetime(2026, 8, 29, 21, 0, tzinfo=UTC)
    return end - timedelta(minutes=int(WINDOW_MINUTES)), end


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class CapturingClient:
    """Records what was asked, and answers only for the tenant that was bound."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.parameters: list[dict[str, Any]] = []

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        self.statements.append(sql)
        self.parameters.append(bound)

        if bound.get("project_id") != PROJECT:
            return FakeQueryResult([], [])
        return FakeQueryResult(EDGE_COLUMNS, _EDGES.get(bound["tenant_id"], []))


def _repository(client: CapturingClient, tenant: str) -> TenantScopedRepository:
    return TenantScopedRepository(client, tenant_id=tenant)  # type: ignore[arg-type]


@pytest.fixture
def client() -> CapturingClient:
    return CapturingClient()


async def _map(client: CapturingClient, tenant: str, limit: int = 200) -> Any:
    start, end = _window()
    return await service.get_service_map(
        _repository(client, tenant),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
        limit=limit,
    )


# ------------------------------------------------------------------- statement


def test_the_edge_statement_filters_on_the_tenant() -> None:
    """The repository would refuse this, but failing here names the culprit."""
    assert binds_tenant(queries.SERVICE_EDGES_SQL)


async def test_the_map_binds_the_session_tenant_and_the_proved_project(
    client: CapturingClient,
) -> None:
    start, end = _window()

    await _map(client, TENANT_A, limit=25)

    bound = client.parameters[-1]
    assert bound["tenant_id"] == TENANT_A
    assert bound["project_id"] == PROJECT
    assert bound["from_time"] == start
    assert bound["to_time"] == end
    assert bound["limit"] == 25


async def test_the_map_binds_no_tenant_it_was_not_given(client: CapturingClient) -> None:
    """Guards the test itself: presence would pass even if the value were ignored."""
    await _map(client, TENANT_A)

    assert OTHER_TENANT not in client.parameters[-1].values()


# ------------------------------------------------------------------- isolation


async def test_two_organizations_cannot_read_each_others_dependencies() -> None:
    for_a = await _map(CapturingClient(), TENANT_A)
    for_b = await _map(CapturingClient(), TENANT_B)

    assert [(edge.caller, edge.callee) for edge in for_a.edges] == [
        ("storefront", "checkout"),
        ("checkout", "payments"),
        ("checkout", "(valkey-cart)"),
    ]
    assert [(edge.caller, edge.callee) for edge in for_b.edges] == [("ledger", "ledger-worker")]


async def test_a_tenant_with_no_dependencies_gets_an_empty_graph_and_not_a_404() -> None:
    empty = await _map(CapturingClient(), OTHER_TENANT)

    assert empty.edges == []
    assert empty.services == []


# ---------------------------------------------------------------------- shapes


async def test_counts_are_reported_as_rates_against_the_window_that_produced_them(
    client: CapturingClient,
) -> None:
    start, end = _window()

    graph = await _map(client, TENANT_A)

    busiest = graph.edges[0]
    assert busiest.call_count == 1200
    assert busiest.calls_per_minute == 1200 / WINDOW_MINUTES
    assert busiest.error_rate == 60 / 1200
    assert (busiest.p50_ms, busiest.p95_ms, busiest.p99_ms) == (12.5, 61.8, 99.0)
    assert graph.window_start == start
    assert graph.window_end == end


async def test_a_queue_hop_keeps_its_own_kind(client: CapturingClient) -> None:
    """A retryable queue hop and a call that fails the caller now are not one edge."""
    graph = await _map(client, TENANT_B)

    assert graph.edges[0].edge_kind == "queue"


async def test_the_nodes_are_exactly_the_services_the_edges_name(
    client: CapturingClient,
) -> None:
    """Querying nodes separately could list a node no edge reaches, or miss one."""
    graph = await _map(client, TENANT_A)

    assert graph.services == ["(valkey-cart)", "checkout", "payments", "storefront"]


async def test_a_callee_that_never_reported_carries_the_technology_it_was_reached_over(
    client: CapturingClient,
) -> None:
    """The one node that used to absorb every unanswered call is now six nodes."""
    graph = await _map(client, TENANT_A)

    peer = next(edge for edge in graph.edges if edge.callee == "(valkey-cart)")
    assert peer.callee_kind == "redis"
    assert all(edge.callee_kind == "" for edge in graph.edges if edge.callee != "(valkey-cart)")


def test_a_name_we_wrote_is_told_apart_from_one_a_service_reported() -> None:
    """A real `service.name` cannot carry parentheses, which is the whole test."""
    assert schemas.is_reserved_service(schemas.UNINSTRUMENTED_SERVICE)
    assert schemas.is_reserved_service(schemas.UNKNOWN_SERVICE)
    assert schemas.is_reserved_service("(valkey-cart)")
    assert not schemas.is_reserved_service("valkey-cart")


async def test_a_service_on_both_ends_of_the_graph_is_listed_once(
    client: CapturingClient,
) -> None:
    graph = await _map(client, TENANT_A)

    assert graph.services.count("checkout") == 1


# --------------------------------------------------------------------- ranking


def test_the_graph_is_truncated_by_traffic_so_a_limit_keeps_its_backbone() -> None:
    """An edge carrying two calls is noise even when both of the two failed."""
    assert queries.BUSIEST_FIRST in queries.SERVICE_EDGES_SQL
    assert queries.BUSIEST_FIRST.startswith("call_count DESC")


# ------------------------------------------------------- agreement with the DDL


def _migration_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in SERVICE_EDGES_MIGRATIONS)


@pytest.mark.parametrize("path", SERVICE_EDGES_MIGRATIONS, ids=lambda path: path.name)
def test_the_migrations_this_suite_checks_against_are_there(path: Path) -> None:
    """A missing file must break this, not silently prove nothing."""
    assert path.is_file(), path


def test_the_merge_asks_for_exactly_the_quantile_levels_the_column_stores() -> None:
    """`quantilesMerge` refuses a state whose levels differ — this catches it here."""
    declared = re.search(r"AggregateFunction\(quantiles\(([^)]*)\)", _migration_text())
    assert declared is not None, "the migration no longer declares a quantiles state"

    levels = tuple(float(level) for level in declared.group(1).split(","))
    assert levels == rollup.QUANTILE_LEVELS
    assert queries.QUANTILE_ARGUMENTS in queries.SERVICE_EDGES_SQL


@pytest.mark.parametrize(
    "column",
    [
        "tenant_id",
        "project_id",
        "client_service",
        "server_service",
        "edge_kind",
        "server_peer_kind",
        "minute",
        "call_count",
        "error_count",
        "latency_quantiles",
    ],
)
def test_every_column_the_statement_names_exists_in_the_table(column: str) -> None:
    """Column names here were read from the DDL, not guessed; this keeps it true."""
    # Either a line of the CREATE, or a column a later ALTER added.
    declaration = re.compile(
        rf"(?:^\s+|ADD COLUMN (?:IF NOT EXISTS )?)`?{column}`?\s", re.MULTILINE
    )

    assert declaration.search(_migration_text()), f"{column} is not a column of service_edges_1m"
    assert column in queries.SERVICE_EDGES_SQL

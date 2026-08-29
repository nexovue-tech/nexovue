"""Service overview reads: the statement, the ranking, and tenant isolation.

Every query here is captured before it reaches ClickHouse and its bound
parameters are inspected. A query that lost its `tenant_id` predicate would
still return correct-looking rows against a store holding one tenant's data — so
asserting on the *statement* is what proves the filter is there, rather than
proving the fixture was lucky.

The column names and the quantile levels are checked against the migration that
created the table, because both are easy to get plausibly wrong: the counters
are pre-aggregated and `quantilesMerge` refuses a level set that is not the
stored one.

`test_services_live.py` runs the same reads against the running stack.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from nexovue_api import rollup
from nexovue_api.data.clickhouse.base import TenantScopedRepository, binds_tenant
from nexovue_api.services import queries, service

TENANT_A: Final = "org-a"
TENANT_B: Final = "org-b"
OTHER_TENANT: Final = "org-nobody"
PROJECT: Final = "project-1"

_MIGRATIONS: Final = Path(__file__).resolve().parents[3] / "deploy" / "clickhouse" / "migrations"
ENDPOINT_STATS_MIGRATION: Final = _MIGRATIONS / "000008_endpoint_stats_1m.up.sql"

WINDOW_MINUTES: Final = 60.0


def _window() -> tuple[datetime, datetime]:
    end = datetime(2026, 8, 29, 21, 0, tzinfo=UTC)
    return end - timedelta(minutes=int(WINDOW_MINUTES)), end


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class CapturingClient:
    """Records what was asked, and answers only for the tenant that was bound.

    Deliberately not a database. Two tenants each own one service, so a query
    whose tenant binding went missing would answer with nothing and the
    isolation tests below would fail rather than pass by luck.
    """

    SERVICE_COLUMNS: Final = ["service_name", "request_count", "error_count", "latency_ms"]
    ENDPOINT_COLUMNS: Final = [
        "service_name",
        "span_kind",
        "span_name",
        "request_count",
        "error_count",
        "latency_ms",
    ]

    def __init__(self, services_by_tenant: dict[str, str]) -> None:
        self._services = services_by_tenant
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

        owned = self._services.get(bound["tenant_id"])
        if owned is None or bound.get("project_id") != PROJECT:
            return FakeQueryResult([], [])
        named = bound.get("service_name")
        if named is not None and named != owned:
            return FakeQueryResult([], [])

        if "span_name" in sql:
            return FakeQueryResult(
                self.ENDPOINT_COLUMNS,
                [(owned, "SERVER", "POST /checkout", 1200, 60, [12.5, 61.8, 99.0])],
            )
        return FakeQueryResult(self.SERVICE_COLUMNS, [(owned, 1200, 60, [12.5, 61.8, 99.0])])


def _client() -> CapturingClient:
    return CapturingClient({TENANT_A: "storefront", TENANT_B: "payments"})


def _repository(client: CapturingClient, tenant: str) -> TenantScopedRepository:
    return TenantScopedRepository(client, tenant_id=tenant)  # type: ignore[arg-type]


@pytest.fixture
def client() -> CapturingClient:
    return _client()


# ------------------------------------------------------------------- statement


def test_every_service_statement_filters_on_the_tenant() -> None:
    """The repository would refuse these, but failing here names the culprit."""
    for sql in (
        queries.SERVICES_SQL,
        queries.endpoints_sql(one_service=False),
        queries.endpoints_sql(one_service=True),
    ):
        assert binds_tenant(sql), sql


async def test_listing_services_binds_the_session_tenant_and_the_proved_project(
    client: CapturingClient,
) -> None:
    start, end = _window()

    await service.list_services(
        _repository(client, TENANT_A),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
        limit=50,
    )

    bound = client.parameters[-1]
    assert bound["tenant_id"] == TENANT_A
    assert bound["project_id"] == PROJECT
    assert bound["from_time"] == start
    assert bound["to_time"] == end
    assert bound["limit"] == 50


async def test_no_service_read_binds_a_tenant_it_was_not_given(
    client: CapturingClient,
) -> None:
    """Guards the test itself: presence would pass even if the value were ignored."""
    start, end = _window()

    await service.list_services(
        _repository(client, TENANT_A),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert OTHER_TENANT not in client.parameters[-1].values()


async def test_naming_a_service_narrows_the_statement_rather_than_the_result(
    client: CapturingClient,
) -> None:
    """Filtering in Python would read every service's rows to show one."""
    start, end = _window()

    await service.list_endpoints(
        _repository(client, TENANT_A),
        project_id=PROJECT,
        service_name="storefront",
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert queries.ONE_SERVICE_CLAUSE in client.statements[-1]
    assert client.parameters[-1]["service_name"] == "storefront"


async def test_asking_for_every_endpoint_binds_no_service_name(
    client: CapturingClient,
) -> None:
    start, end = _window()

    await service.list_endpoints(
        _repository(client, TENANT_A),
        project_id=PROJECT,
        service_name=None,
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert queries.ONE_SERVICE_CLAUSE not in client.statements[-1]
    assert "service_name" not in client.parameters[-1]


# ------------------------------------------------------------------- isolation


async def test_two_organizations_cannot_read_each_others_services() -> None:
    start, end = _window()

    for_a = await service.list_services(
        _repository(_client(), TENANT_A),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
        limit=50,
    )
    for_b = await service.list_services(
        _repository(_client(), TENANT_B),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert [row.service_name for row in for_a.services] == ["storefront"]
    assert [row.service_name for row in for_b.services] == ["payments"]


async def test_a_tenant_with_no_telemetry_gets_an_empty_list_and_not_a_404() -> None:
    """ "Nothing yet" is the normal state during onboarding."""
    start, end = _window()

    page = await service.list_services(
        _repository(_client(), OTHER_TENANT),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert page.services == []


async def test_an_unknown_service_name_is_an_empty_list_not_an_error() -> None:
    """The rollup knows which services reported *in this window*, not which exist."""
    start, end = _window()

    page = await service.list_endpoints(
        _repository(_client(), TENANT_A),
        project_id=PROJECT,
        service_name="never-deployed",
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert page.endpoints == []


# ---------------------------------------------------------------------- shapes


async def test_counts_are_reported_as_rates_against_the_window_that_produced_them(
    client: CapturingClient,
) -> None:
    start, end = _window()

    page = await service.list_services(
        _repository(client, TENANT_A),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
        limit=50,
    )

    row = page.services[0]
    assert row.request_count == 1200
    assert row.requests_per_minute == 1200 / WINDOW_MINUTES
    assert row.error_rate == 60 / 1200
    assert page.window_start == start
    assert page.window_end == end


async def test_the_stored_quantile_state_is_unpacked_in_the_declared_order(
    client: CapturingClient,
) -> None:
    """A p95 read out of the wrong array position is wrong and looks plausible."""
    start, end = _window()

    page = await service.list_endpoints(
        _repository(client, TENANT_A),
        project_id=PROJECT,
        service_name=None,
        from_time=start,
        to_time=end,
        limit=50,
    )

    endpoint = page.endpoints[0]
    assert (endpoint.p50_ms, endpoint.p95_ms, endpoint.p99_ms) == (12.5, 61.8, 99.0)
    assert endpoint.endpoint == "POST /checkout"
    assert endpoint.span_kind == "SERVER"


def test_a_group_that_merged_no_state_reports_zero_rather_than_raising() -> None:
    """ClickHouse returns an empty array for an empty merge; a crash is not an answer."""
    empty = rollup.Latency([])

    assert (empty.p50_ms, empty.p95_ms, empty.p99_ms) == (0.0, 0.0, 0.0)


def test_a_service_with_no_requests_has_no_error_rate_rather_than_a_division() -> None:
    assert rollup.failure_ratio(0, 0) == 0.0


# --------------------------------------------------------------------- ranking


def test_both_rankings_put_the_worst_first_by_the_same_rule() -> None:
    """One rule, spelled once: the service list and the endpoint list must agree."""
    assert queries.WORST_FIRST in queries.SERVICES_SQL
    assert queries.WORST_FIRST in queries.endpoints_sql(one_service=False)
    assert queries.WORST_FIRST in queries.endpoints_sql(one_service=True)


def test_the_ranking_leads_on_failures_and_breaks_ties_on_the_slow_tail() -> None:
    """The ordering is the product; a silent change to it changes the screen."""
    assert queries.WORST_FIRST.startswith("error_count DESC")
    assert f"latency_ms[{rollup.P95_INDEX}] DESC" in queries.WORST_FIRST
    assert "request_count DESC" in queries.WORST_FIRST


# ------------------------------------------------------- agreement with the DDL


def _migration_text() -> str:
    return ENDPOINT_STATS_MIGRATION.read_text(encoding="utf-8")


def test_the_migration_this_suite_checks_against_is_there() -> None:
    """A missing file must break this, not silently prove nothing."""
    assert ENDPOINT_STATS_MIGRATION.is_file(), ENDPOINT_STATS_MIGRATION


def test_the_merge_asks_for_exactly_the_quantile_levels_the_column_stores() -> None:
    """`quantilesMerge` refuses a state whose levels differ — this catches it here."""
    declared = re.search(r"AggregateFunction\(quantiles\(([^)]*)\)", _migration_text())
    assert declared is not None, "the migration no longer declares a quantiles state"

    levels = tuple(float(level) for level in declared.group(1).split(","))
    assert levels == rollup.QUANTILE_LEVELS
    assert queries.QUANTILE_ARGUMENTS in queries.SERVICES_SQL


@pytest.mark.parametrize(
    "column",
    [
        "tenant_id",
        "project_id",
        "service_name",
        "span_kind",
        "span_name",
        "minute",
        "request_count",
        "error_count",
        "latency_quantiles",
    ],
)
def test_every_column_the_statements_name_exists_in_the_table(column: str) -> None:
    """Column names here were read from the DDL, not guessed; this keeps it true."""
    declaration = re.compile(rf"^\s+`?{column}`?\s", re.MULTILINE)

    assert declaration.search(_migration_text()), f"{column} is not a column of endpoint_stats_1m"
    assert column in queries.SERVICES_SQL or column in queries.endpoints_sql(one_service=True)

"""The metrics catalogue: one question across five tables.

The fake answers from the values the repository *bound*, never from the
statement text, so a read that lost its tenant predicate finds nothing and the
isolation test below fails rather than quietly passing.

`test_metrics_live.py` runs the same statement against the real ClickHouse.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_api.data.clickhouse.base import TENANT_BINDING, TenantScopedRepository
from nexovue_api.metrics import queries, service
from nexovue_api.metrics.schemas import MetricCatalogueResponse

TENANT_A: Final = "org-a"
TENANT_B: Final = "org-b"
PROJECT_A: Final = "project-a"
PROJECT_B: Final = "project-b"

_LATEST_NANOS: Final = 1_788_038_991_898_000_000

_COLUMNS: Final = [
    "metric_name",
    "shapes",
    "metric_unit",
    "series",
    "points",
    "latest_nanos",
]


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


# Each tenant owns exactly one project. A read that arrives with the wrong pair
# gets nothing back, which is what makes a dropped predicate visible as an empty
# answer rather than as somebody else's metrics.
_OWNED_PROJECT: Final = {TENANT_A: PROJECT_A, TENANT_B: PROJECT_B}


class FakeMetricStore:
    """Serves a catalogue, honouring the tenant and project the query bound."""

    def __init__(self, rows_by_tenant: dict[str, list[tuple[Any, ...]]]) -> None:
        self._rows = rows_by_tenant
        self.last_parameters: dict[str, Any] = {}
        self.statements: list[str] = []

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        self.last_parameters = bound
        self.statements.append(sql)

        tenant = bound["tenant_id"]
        if _OWNED_PROJECT.get(tenant) != bound.get("project_id"):
            return FakeQueryResult([], [])
        return FakeQueryResult(_COLUMNS, self._rows.get(tenant, []))


def _repository(tenant: str, store: FakeMetricStore) -> TenantScopedRepository:
    return TenantScopedRepository(store, tenant_id=tenant)  # type: ignore[arg-type]


def _window() -> tuple[datetime, datetime]:
    end = datetime(2026, 8, 27, 11, 0, tzinfo=UTC)
    return end - timedelta(hours=1), end


def _row(
    name: str,
    shapes: list[str],
    *,
    unit: str = "ms",
    series: int = 4,
    points: int = 400,
) -> tuple[Any, ...]:
    return (name, shapes, unit, series, points, _LATEST_NANOS)


def _store(rows: list[tuple[Any, ...]] | None = None) -> FakeMetricStore:
    return FakeMetricStore(
        {
            TENANT_A: rows if rows is not None else [_row("http.server.duration", ["histogram"])],
            TENANT_B: [_row("other.tenant.metric", ["gauge"])],
        }
    )


async def _catalogue(
    store: FakeMetricStore, tenant: str = TENANT_A, project: str = PROJECT_A
) -> MetricCatalogueResponse:
    start, end = _window()
    return await service.list_metrics(
        _repository(tenant, store), project_id=project, from_time=start, to_time=end
    )


# ------------------------------------------------------------------- statement


def test_the_catalogue_statement_binds_the_tenant_in_every_branch() -> None:
    """Five tables, five predicates. One branch without it is a whole table leaked.

    The repository refuses a statement missing the binding entirely, which would
    not catch four branches that have it and one that does not — so this counts.
    """
    assert queries.CATALOGUE_SQL.count(TENANT_BINDING) == 5


def test_every_metric_table_is_in_the_catalogue() -> None:
    """A table left out is a customer's metrics invisible with nothing to say so.

    Named explicitly rather than derived, because the point of the screen is
    that `metrics_histogram`, `metrics_exp_histogram` and `metrics_summary` were
    written for months with no reader at all.
    """
    for table in (
        "metrics_gauge",
        "metrics_sum",
        "metrics_histogram",
        "metrics_exp_histogram",
        "metrics_summary",
    ):
        assert f"FROM {table}\n" in queries.CATALOGUE_SQL


def test_the_counts_are_over_distinct_readings_and_never_rows() -> None:
    """A metric row is not unique, and a redelivered batch must not inflate this.

    `metricwriting.Absorb` takes a duplicate over a lost point and leaves the
    reader to group by series and timestamp first. `count()` here would report a
    replayed hour as twice the traffic.

    `series_hash` rather than the attributes as JSON: the same answer, and 0.09
    seconds against 2.29 over six hours of the development project.
    """
    assert "uniqExact((series_hash, observed_nanos))" in queries.CATALOGUE_SQL
    assert "count()" not in queries.CATALOGUE_SQL


# ------------------------------------------------------------------- isolation


async def test_another_tenant_sees_none_of_these_metrics() -> None:
    store = _store()

    stolen = await _catalogue(store, tenant=TENANT_B, project=PROJECT_A)

    assert stolen.metrics == []


async def test_the_read_binds_the_session_tenant_and_the_proved_project() -> None:
    store = _store()

    await _catalogue(store)

    assert store.last_parameters["tenant_id"] == TENANT_A
    assert store.last_parameters["project_id"] == PROJECT_A


# --------------------------------------------------------------------- content


async def test_a_name_sent_in_two_shapes_is_one_row_naming_both() -> None:
    """It is one metric with a configuration problem, not two metrics.

    Nothing stops a service sending `queue.depth` as a gauge and as a sum. A
    catalogue that picked one would hide the half it discarded, and the half it
    discarded is the finding.
    """
    store = _store([_row("queue.depth", ["gauge", "sum"], unit="")])

    catalogue = await _catalogue(store)

    assert len(catalogue.metrics) == 1
    assert catalogue.metrics[0].shapes == ["gauge", "sum"]


async def test_a_shape_with_no_reader_is_listed_and_says_so() -> None:
    """A metric a customer is paying to store should appear even undrawable.

    Hiding it answers "we have nothing" to a customer who is sending something,
    which is the worst of the three possible answers.
    """
    store = _store(
        [
            _row("http.server.duration", ["histogram"]),
            _row("request.size", ["summary"]),
        ]
    )

    catalogue = await _catalogue(store)
    readable = {metric.metric_name: metric.readable for metric in catalogue.metrics}

    assert readable == {"http.server.duration": True, "request.size": False}


async def test_a_name_in_a_readable_and_an_unreadable_shape_is_not_readable() -> None:
    """It cannot be drawn as one thing, and saying otherwise promises a drawing.

    The screen would open it, find two incompatible shapes, and have nothing to
    render — a dead end reached by following a label that said it was fine.
    """
    store = _store([_row("mixed.metric", ["histogram", "summary"])])

    catalogue = await _catalogue(store)

    assert catalogue.metrics[0].readable is False


async def test_a_project_that_reported_nothing_is_an_empty_list_not_an_error() -> None:
    """An empty table contributes nothing rather than a row of zeroes.

    Two of the five tables are empty in every deployment we have — nothing sends
    an exponential histogram or a summary — so a catalogue that materialised a
    zero row per table would be three-fifths noise on every screen.
    """
    store = _store([])

    catalogue = await _catalogue(store)

    assert catalogue.metrics == []
    assert catalogue.truncated is False


async def test_a_full_catalogue_says_it_was_cut() -> None:
    """Read one past the cap, so a full page is told apart from a truncated one."""
    store = _store([_row(f"metric.{index}", ["gauge"]) for index in range(service.MAX_METRICS + 1)])

    catalogue = await _catalogue(store)

    assert len(catalogue.metrics) == service.MAX_METRICS
    assert catalogue.truncated is True


async def test_the_window_is_bounded_on_our_receive_clock_as_well() -> None:
    """The customer supplies `timestamp` and cannot prune a partition with it."""
    store = _store()

    await _catalogue(store)

    assert store.last_parameters["received_from"] < store.last_parameters["from_time"]
    assert store.last_parameters["received_to"] > store.last_parameters["to_time"]

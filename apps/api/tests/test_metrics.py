"""The metrics catalogue: one question across five tables.

The fake answers from the values the repository *bound*, never from the
statement text, so a read that lost its tenant predicate finds nothing and the
isolation test below fails rather than quietly passing.

`test_metrics_live.py` runs the same statement against the real ClickHouse.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final, get_args

import pytest

from nexovue_api.data.clickhouse.base import TENANT_BINDING, TenantScopedRepository
from nexovue_api.metrics import queries, router, service
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
    store: FakeMetricStore,
    tenant: str = TENANT_A,
    project: str = PROJECT_A,
    **narrowing: Any,
) -> MetricCatalogueResponse:
    start, end = _window()
    return await service.list_metrics(
        _repository(tenant, store),
        project_id=project,
        from_time=start,
        to_time=end,
        **narrowing,
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


# ----------------------------------------------------------------------- filters
#
# All three predicates are inside the statement, ahead of its `LIMIT`. The
# catalogue is cut at `MAX_METRICS`, so asking "which of these are gauges" of the
# rows that came back would answer "the gauges among the busiest five hundred" —
# and `truncated` beside it would still claim the cut was about traffic.


def test_an_unfiltered_catalogue_carries_none_of_the_predicates() -> None:
    """A spliced clause with nothing bound to it is a failed read, not a filter."""
    statement = queries.catalogue_sql(name_match=False, shape=False, readable_only=False)

    assert "name_match:String" not in statement
    assert "HAVING" not in statement


async def test_an_unfiltered_catalogue_binds_none_of_them_either() -> None:
    store = _store()

    await _catalogue(store)

    for binding in ("name_match", "shape", "readable_shapes"):
        assert binding not in store.last_parameters


def test_a_typed_name_narrows_every_branch_before_anything_is_grouped() -> None:
    """Five tables, five predicates — and all five ahead of the group-by.

    Matched anywhere in the name and case-insensitively: somebody looking for
    the queue metrics types "queue", not the prefix of a name they have not
    read yet.
    """
    statement = queries.catalogue_sql(name_match=True, shape=False, readable_only=False)

    assert statement.count("positionCaseInsensitive(metric_name") == 5
    assert statement.index("positionCaseInsensitive") < statement.index("GROUP BY metric_name")


async def test_a_typed_name_reaches_the_statement_rather_than_the_answer() -> None:
    store = _store()

    await _catalogue(store, name_match="queue")

    assert store.last_parameters["name_match"] == "queue"
    assert "name_match:String" in store.statements[-1]


def test_the_shape_filter_asks_the_grouped_shapes_and_never_the_rows() -> None:
    """Narrowing the rows would hide the conflict this screen exists to show.

    A name sent as a histogram and a summary would come back from a row-level
    filter looking like a clean histogram — and `readable`, computed from the
    shapes that survived, would then offer a chart that cannot be drawn.
    """
    statement = queries.catalogue_sql(name_match=False, shape=True, readable_only=False)

    assert "HAVING has(shapes, {shape:String})" in statement
    assert statement.index("HAVING") > statement.index("GROUP BY metric_name")
    assert statement.index("HAVING") < statement.index("LIMIT")


def test_readable_only_is_the_same_rule_the_readable_flag_is_computed_from() -> None:
    """The filter and the column beside it must not be able to disagree.

    `_to_summary` calls a name readable when every shape it arrived in has a
    reader; this clause says the same thing in SQL, over the set that rule uses.
    """
    statement = queries.catalogue_sql(name_match=False, shape=False, readable_only=True)

    assert "arrayAll(" in statement
    assert "readable_shapes:Array(String)" in statement


async def test_readable_only_binds_the_readable_shapes_themselves() -> None:
    """Bound rather than written into the statement, so adding a reader to
    `READABLE_SHAPES` widens the filter with it."""
    store = _store()

    await _catalogue(store, readable_only=True)

    assert store.last_parameters["readable_shapes"] == sorted(queries.READABLE_SHAPES)


def test_two_narrowings_are_one_having_and_not_two() -> None:
    statement = queries.catalogue_sql(name_match=True, shape=True, readable_only=True)

    assert statement.count("HAVING") == 1
    assert " AND " in statement[statement.index("HAVING") :]


def test_no_narrowing_costs_the_statement_its_tenant_binding() -> None:
    """Five branches, five predicates, whatever was spliced into them."""
    for name_match, shape, readable_only in ((False, False, False), (True, True, True)):
        statement = queries.catalogue_sql(
            name_match=name_match, shape=shape, readable_only=readable_only
        )
        assert statement.count(TENANT_BINDING) == 5


def test_the_api_accepts_exactly_the_shapes_the_catalogue_can_report() -> None:
    """A shape the reader can pick and the store cannot produce is an empty
    catalogue that reads as "this project reports no gauges"."""
    assert get_args(router.MetricShape) == queries.ALL_SHAPES


# ------------------------------------------------------- estimating off buckets


class TestQuantilesOffBuckets:
    """What a percentile read from a histogram can and cannot claim.

    Every number here is an estimate, and the tests are written to pin the
    places where that matters rather than to restate the arithmetic.
    """

    def test_a_rank_inside_a_bucket_is_placed_linearly_within_it(self) -> None:
        """The whole of the estimation, in one case with an obvious answer.

        Ten observations, all in the bucket from 10 to 20. The median rank falls
        halfway through that bucket, so the estimate is halfway along it.
        """
        estimates = service.estimate_quantiles([10.0, 20.0], [0, 10, 0], 10)

        assert estimates[0] == pytest.approx(15.0)

    def test_an_empty_histogram_estimates_nothing_rather_than_zero(self) -> None:
        """Zero is a latency somebody would act on. Absence is not.

        A window in which nothing was observed must not report a p99 of 0ms —
        the screen renders an em dash for None and a fast service for 0.
        """
        assert service.estimate_quantiles([10.0, 20.0], [0, 0, 0], 0) == (None,) * 5
        assert service.estimate_quantiles([10.0, 20.0], [0, 0, 0], 7) == (None,) * 5

    def test_the_first_bucket_is_read_as_starting_at_zero(self) -> None:
        """It has no lower bound, and every metric read here is a duration or a
        size. Interpolating from negative infinity would be arithmetic nobody
        can use."""
        estimates = service.estimate_quantiles([100.0], [10, 0], 10)

        assert estimates[0] == pytest.approx(50.0)

    def test_a_rank_above_the_last_bound_reports_the_bound_and_not_infinity(self) -> None:
        """The last bucket is open-ended, so the honest answer is a floor.

        Nine observations under 10 and one above it: the p99 lands in the
        unbounded bucket, where the only defensible claim is "at least 10".
        """
        estimates = service.estimate_quantiles([1.0, 10.0], [0, 9, 1], 10)

        assert estimates[4] == pytest.approx(10.0)

    def test_a_histogram_with_no_bounds_at_all_does_not_raise(self) -> None:
        """A single unbounded bucket is legal OTLP and is in the store —
        `otel.sdk.exporter.operation.duration` arrives with none."""
        assert service.estimate_quantiles([], [12], 12) == (0.0,) * 5

    def test_the_estimates_never_go_backwards(self) -> None:
        """A p95 below a p50 would be visibly wrong on the screen, and is the
        shape a bad interpolation takes."""
        counts = [3, 12, 40, 21, 9, 2]
        estimates = service.estimate_quantiles([1.0, 5.0, 25.0, 100.0, 500.0], counts, sum(counts))

        values_read = [estimate for estimate in estimates if estimate is not None]
        assert values_read == sorted(values_read)
        assert len(values_read) == 5


# ------------------------------------------------------ merging the buckets


_HISTOGRAM_COLUMNS: Final = [
    "bounds",
    "services",
    "series",
    "bucket_counts",
    "total_count",
    "total_sum",
    "low",
    "high",
    "latest_nanos",
]

_IDENTITY_COLUMNS: Final = ["metric_unit", "metric_description"]


class FakeHistogramStore:
    """Answers each statement the histogram read issues, and only that one.

    **Dispatch is on the statement itself, not a substring of it.** A fake that
    matched `"GROUP BY"` once answered every statement in `synthetics` as the
    summary, and the misrouting survived because the shapes happened to fit.
    These two share most of their text.
    """

    def __init__(
        self, layouts: list[tuple[Any, ...]], identity: tuple[Any, ...] = ("ms", "how long")
    ) -> None:
        self._layouts = layouts
        self._identity = identity
        self.statements: list[str] = []
        self.last_parameters: dict[str, Any] = {}

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        self.statements.append(sql)
        self.last_parameters = dict(parameters or {})

        if sql == queries.HISTOGRAM_SQL:
            return FakeQueryResult(_HISTOGRAM_COLUMNS, self._layouts)
        if sql == queries.HISTOGRAM_IDENTITY_SQL:
            return FakeQueryResult(_IDENTITY_COLUMNS, [self._identity])
        raise AssertionError(
            f"the histogram read issued a statement this fake does not know:\n{sql}"
        )


def _layout(
    bounds: list[float],
    counts: list[int],
    *,
    services: list[str],
    total: int | None = None,
    total_sum: float | None = 12.5,
    low: float | None = 0.5,
    high: float | None = 900.0,
    series: int = 2,
) -> tuple[Any, ...]:
    return (
        bounds,
        services,
        series,
        counts,
        sum(counts) if total is None else total,
        total_sum,
        low,
        high,
        _LATEST_NANOS,
    )


async def _histogram(store: FakeHistogramStore, name: str = "db.client.operation.duration") -> Any:
    start, end = _window()
    return await service.read_histogram(
        _repository(TENANT_A, store),  # type: ignore[arg-type]
        project_id=PROJECT_A,
        metric_name=name,
        from_time=start,
        to_time=end,
    )


class TestTheHistogramRead:
    async def test_one_layout_is_a_merged_distribution(self) -> None:
        store = FakeHistogramStore([_layout([1.0, 10.0], [2, 6, 2], services=["checkout"])])

        answer = await _histogram(store)

        assert answer.merged is True
        assert len(answer.layouts) == 1
        assert answer.layouts[0].bucket_counts == [2, 6, 2]
        assert answer.layouts[0].count == 10
        assert answer.unit == "ms"

    async def test_two_bucket_layouts_are_reported_apart_and_never_added(self) -> None:
        """The case that happens in real data.

        `db.client.operation.duration` arrives from one service with bounds in
        milliseconds and from another with bounds in seconds. Adding their
        counts would add milliseconds to seconds, so each is answered whole and
        the response says nothing was merged.
        """
        store = FakeHistogramStore(
            [
                _layout([0.0, 5.0, 10.0], [0, 100, 20, 3], services=["product-catalog"]),
                _layout([0.001, 0.005, 0.01], [6, 93, 49, 29], services=["accounting"]),
            ]
        )

        answer = await _histogram(store)

        assert answer.merged is False, "two bucket layouts are not one distribution"
        assert len(answer.layouts) == 2
        assert [layout.services for layout in answer.layouts] == [
            ["product-catalog"],
            ["accounting"],
        ]
        # Each layout keeps its own totals; nothing is a sum across the two.
        assert [layout.count for layout in answer.layouts] == [123, 177]

    async def test_a_conflict_names_the_services_that_disagree(self) -> None:
        """The actionable half. "Two layouts" is a symptom; which services chose
        them is what somebody can go and fix."""
        store = FakeHistogramStore(
            [
                _layout([0.0, 5.0], [1, 1, 1], services=["product-catalog"]),
                _layout([0.001, 0.005], [1, 1, 1], services=["accounting", "cart"]),
            ]
        )

        answer = await _histogram(store)

        assert answer.layouts[1].services == ["accounting", "cart"]

    async def test_a_metric_that_reported_nothing_is_an_empty_answer_not_an_error(self) -> None:
        """A name with no points in the window is a quiet metric, not a bad
        request. The screen says "nothing in this window" and keeps the name."""
        store = FakeHistogramStore([])

        answer = await _histogram(store, name="never.reported")

        assert answer.layouts == []
        assert answer.merged is True
        assert answer.metric_name == "never.reported"

    async def test_a_negative_bucket_never_reaches_the_response(self) -> None:
        """A window subtraction can go slightly negative when a series was
        re-exported out of order. A negative count in a distribution is not a
        reading anybody can act on."""
        store = FakeHistogramStore([_layout([1.0, 10.0], [-2, 6, 2], services=["cart"], total=-1)])

        answer = await _histogram(store)

        assert answer.layouts[0].bucket_counts == [0, 6, 2]
        assert answer.layouts[0].count == 0

    async def test_an_absent_sum_stays_absent_rather_than_becoming_zero(self) -> None:
        """OTLP does not require a histogram to carry a sum, and zero is a
        reading somebody would act on."""
        store = FakeHistogramStore(
            [_layout([1.0], [1, 1], services=["cart"], total_sum=None, low=None, high=None)]
        )

        answer = await _histogram(store)

        assert answer.layouts[0].sum is None
        assert answer.layouts[0].min is None
        assert answer.layouts[0].max is None

    async def test_the_read_is_bound_to_the_metric_the_caller_named(self) -> None:
        store = FakeHistogramStore([_layout([1.0], [1, 1], services=["cart"])])

        await _histogram(store, name="http.server.duration")

        assert store.last_parameters["metric_name"] == "http.server.duration"
        assert store.last_parameters["tenant_id"] == TENANT_A


class TestTheHistogramStatements:
    def test_every_histogram_statement_binds_the_tenant(self) -> None:
        """The chokepoint rule, checked here too: the structural test parses
        source, and a statement that stopped naming the tenant would otherwise
        only fail once it had read somebody else's rows."""
        for statement in (queries.HISTOGRAM_SQL, queries.HISTOGRAM_IDENTITY_SQL):
            assert TENANT_BINDING in statement

    def test_the_window_statement_groups_by_the_bucket_layout(self) -> None:
        """Losing this `GROUP BY` is what would silently add two distributions
        together, and the result would look like a plausible histogram."""
        assert "GROUP BY bounds" in queries.HISTOGRAM_SQL
        assert "GROUP BY series_hash, explicit_bounds" in queries.HISTOGRAM_SQL

    def test_the_distribution_is_a_window_delta_and_not_a_sum_of_points(self) -> None:
        """Every histogram in the store is cumulative. Summing points across an
        hour of ten-second exports reports 360 times the traffic."""
        assert "argMax(bucket_counts, timestamp)" in queries.HISTOGRAM_SQL
        assert "argMin(bucket_counts, timestamp)" in queries.HISTOGRAM_SQL


# ------------------------------------------------------ the conditional heatmap


_HEATMAP_COLUMNS: Final = [
    "metric_name",
    "metric_unit",
    "bounds",
    "bucket_nanos",
    "counts",
    "total",
]


class FakeHeatmapStore:
    """Answers the heatmap statement and nothing else."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows
        self.last_parameters: dict[str, Any] = {}

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        self.last_parameters = dict(parameters or {})
        if sql == queries.HEATMAP_SQL:
            return FakeQueryResult(_HEATMAP_COLUMNS, self._rows)
        raise AssertionError(f"the heatmap read issued an unexpected statement:\n{sql}")


def _column(
    bucket_nanos: int,
    counts: list[int],
    *,
    bounds: list[float] | None = None,
    metric_name: str = "http.server.request.duration",
    unit: str = "ms",
) -> tuple[Any, ...]:
    return (
        metric_name,
        unit,
        [1.0, 5.0, 25.0] if bounds is None else bounds,
        bucket_nanos,
        counts,
        sum(counts),
    )


async def _heatmap(store: FakeHeatmapStore, service_name: str = "checkout") -> Any:
    start, end = _window()
    return await service.read_duration_heatmap(
        _repository(TENANT_A, store),  # type: ignore[arg-type]
        project_id=PROJECT_A,
        service_name=service_name,
        from_time=start,
        to_time=end,
    )


class TestTheConditionalHeatmap:
    async def test_a_service_sending_histograms_gets_a_panel(self) -> None:
        store = FakeHeatmapStore(
            [
                _column(_LATEST_NANOS, [1, 4, 2, 0]),
                _column(_LATEST_NANOS + 60_000_000_000, [0, 6, 3, 1]),
            ]
        )

        answer = await _heatmap(store)

        assert answer.available is True
        assert len(answer.columns) == 2
        assert answer.bounds == [1.0, 5.0, 25.0]
        assert answer.columns[0].total == 7
        # Without the exporter's own unit a bound renders as a bare number, and
        # `10000` reads as ten thousand of something rather than ten seconds.
        assert answer.unit == "ms"

    async def test_a_service_sending_none_has_no_panel_rather_than_an_empty_one(self) -> None:
        """OD7 says absent, not empty.

        An empty heatmap and a service with no traffic are the same picture, and
        only one of them is the truth. The screen is told which it holds.
        """
        answer = await _heatmap(FakeHeatmapStore([]))

        assert answer.available is False
        assert answer.columns == []
        assert answer.bounds == []

    async def test_both_spellings_of_the_metric_are_asked_for(self) -> None:
        """OTel renamed this metric and both names are in the wild — an SDK's
        version decides which it emits. Asking for one shows the panel to a
        fraction of the services that can have it."""
        store = FakeHeatmapStore([])

        await _heatmap(store)

        assert set(store.last_parameters["metric_names"]) == {
            "http.server.duration",
            "http.server.request.duration",
        }

    async def test_the_read_starts_one_bucket_before_the_window(self) -> None:
        """The first column needs a predecessor to subtract, or it carries the
        whole history since the exporter started."""
        store = FakeHeatmapStore([])
        start, _ = _window()

        answer = await _heatmap(store)

        read_from = store.last_parameters["read_from"]
        assert read_from < start
        assert (start - read_from).total_seconds() == answer.bucket_seconds

    async def test_two_layouts_are_not_drawn_as_one_panel(self) -> None:
        """A heatmap is a single panel, so it takes the busiest layout rather
        than adding two distributions measured on different scales together.
        The catalogue is where a reader sees that there were several."""
        store = FakeHeatmapStore(
            [
                _column(_LATEST_NANOS, [1, 1], bounds=[10.0]),
                _column(_LATEST_NANOS, [500, 400], bounds=[0.01]),
            ]
        )

        answer = await _heatmap(store)

        assert answer.bounds == [0.01]
        assert len(answer.columns) == 1
        assert answer.columns[0].total == 900

    async def test_a_negative_column_never_reaches_the_response(self) -> None:
        """A restart between two buckets can leave a subtraction below zero, and
        a negative count is not a reading anybody can draw."""
        store = FakeHeatmapStore([_column(_LATEST_NANOS, [-3, 4, 1, 0])])

        answer = await _heatmap(store)

        assert answer.columns[0].counts == [0, 4, 1, 0]

    def test_the_statement_binds_the_tenant_and_reads_one_service(self) -> None:
        assert TENANT_BINDING in queries.HEATMAP_SQL
        assert "service_name = {service_name:String}" in queries.HEATMAP_SQL

    def test_the_statement_subtracts_the_previous_bucket(self) -> None:
        """Losing this makes every column the total since start-up: monotonically
        growing, identical in shape, and a heatmap of nothing."""
        assert "lagInFrame" in queries.HEATMAP_SQL
        assert "PARTITION BY metric_name, explicit_bounds, series_hash" in queries.HEATMAP_SQL

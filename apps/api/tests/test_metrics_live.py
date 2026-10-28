"""The catalogue against the real ClickHouse.

The offline suite proves the statement is scoped and the shapes are folded
correctly. It cannot tell you the `UNION ALL` across five tables parses, that
every table really carries the columns the branch names, or that the counts
describe what is stored. This one asks the store.

It fails when the stack is not up rather than skipping, because a skip here is
indistinguishable from a pass. `NEXOVUE_SKIP_LIVE_TESTS=1` opts out on purpose.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
import pytest_asyncio
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.metrics import queries, service
from nexovue_api.metrics.schemas import (
    DurationHeatmapResponse,
    HistogramResponse,
    MetricCatalogueResponse,
)

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_HTTP_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0
LOOKBACK: Final = timedelta(days=2)


def _reachable() -> bool:
    parts = urlsplit(CLICKHOUSE_DSN)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or 8123), REACHABILITY_TIMEOUT_SECONDS
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_metrics_live.py",
    remedy=f"ClickHouse is not answering on {CLICKHOUSE_DSN} — start it with `just dev`",
)


@pytest_asyncio.fixture
async def clickhouse() -> AsyncIterator[AsyncClient]:
    parts = urlsplit(CLICKHOUSE_DSN)
    client = await clickhouse_connect.get_async_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "",
        password=parts.password or "",
        database=(parts.path or "/").lstrip("/"),
    )
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture
async def reporting(clickhouse: AsyncClient) -> tuple[str, str]:
    """The tenant and project with the most server-duration points, found with a raw read.

    Server durations rather than any histogram, because the heatmap reads only those
    (`queries.DURATION_METRIC_NAMES`): on a fresh machine the project with the most
    histogram points need not send one (CI, 2026-10-28). The gate seeds them
    (tools/gateseed), and the demo shop sends them on a developer's machine.
    Asking "who has metrics" needs an unscoped query, which the repository
    rightly refuses, so it is fixture setup rather than a claim about the API.
    """
    result = await clickhouse.query(
        "SELECT tenant_id, project_id, count() AS points FROM metrics_histogram "
        "WHERE timestamp >= %(since)s AND metric_name IN %(names)s "
        "GROUP BY tenant_id, project_id ORDER BY points DESC LIMIT 1",
        parameters={
            "since": datetime.now(UTC) - LOOKBACK,
            "names": list(queries.DURATION_METRIC_NAMES),
        },
    )
    assert result.result_rows, (
        "no histogram points in ClickHouse for the last two days. This suite asserts "
        "on real instrumentation, so an empty store is a failure here rather than a "
        "pass — run the load generator."
    )
    tenant_id, project_id, _ = result.result_rows[0]
    return str(tenant_id), str(project_id)


@pytest_asyncio.fixture
async def histogram_metric(clickhouse: AsyncClient, reporting: tuple[str, str]) -> str:
    """The histogram with the most points in this tenant's window.

    **Named, not hard-coded.** Four tests here asked for `k6.http_req_duration`,
    which is the OpenTelemetry demo's load generator's own latency — so the suite
    could only run after somebody had started twenty-one containers by hand, and
    turned red on a clean tree once that data aged out of the window. The subject
    is *a real histogram*, not that one: whatever a generator has put here, the
    read must fold it over its own bounds. `just gate-seed` supplies one, and the
    demo still satisfies this when it is running.
    """
    tenant_id, project_id = reporting
    start, end = _window()
    result = await clickhouse.query(
        # **`explicit_bounds` must not be empty.** A real store holds histograms
        # with no bounds at all — an exponential one folds into a single
        # unbounded bucket — and these tests are about folding a distribution
        # *over its bounds*. Discovered rather than assumed: the first run of
        # this fixture picked `ad`/`kafka`'s bucketless histogram and the merge
        # test failed on correct behaviour.
        "SELECT metric_name FROM metrics_histogram "
        "WHERE tenant_id = %(tenant)s AND project_id = %(project)s "
        "AND timestamp >= %(since)s AND timestamp <= %(until)s "
        "AND notEmpty(explicit_bounds) "
        "GROUP BY metric_name ORDER BY count() DESC LIMIT 1",
        parameters={"tenant": tenant_id, "project": project_id, "since": start, "until": end},
    )
    assert result.result_rows, (
        "no bucketed histogram points in this tenant's window. This suite asserts on "
        "real instrumentation, so an empty store is a failure here rather than a pass "
        "— run `just gate-seed`, which is what the gate does."
    )
    return str(result.result_rows[0][0])


def _window() -> tuple[datetime, datetime]:
    end = datetime.now(UTC)
    return end - LOOKBACK, end


async def _catalogue(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> MetricCatalogueResponse:
    tenant_id, project_id = reporting
    start, end = _window()
    return await service.list_metrics(
        TenantScopedRepository(clickhouse, tenant_id=tenant_id),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )


async def test_the_catalogue_reads_all_five_tables_in_one_statement(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """The `UNION ALL` parses and every branch's columns exist.

    Two of the five tables are empty in every deployment we have, so this is the
    assertion that would catch a column named wrongly in a branch nothing
    returns rows from — a defect no amount of real data reveals.
    """
    catalogue = await _catalogue(clickhouse, reporting)

    assert catalogue.metrics, "the busiest project reported no metrics at all"


async def test_a_histogram_a_customer_sent_is_in_the_catalogue_and_readable(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """The AC5 gap, stated as a test.

    `metrics_histogram` has been written, masked and retained with no reader at
    all. A customer sending an OTLP histogram — the standard latency shape from
    every OTel SDK — had it stored and could never see it.
    """
    catalogue = await _catalogue(clickhouse, reporting)
    histograms = [metric for metric in catalogue.metrics if metric.shapes == ["histogram"]]

    assert histograms, "no histogram in the catalogue, though the fixture found one stored"
    assert all(metric.readable for metric in histograms)
    assert all(metric.points > 0 and metric.series > 0 for metric in histograms)


async def test_a_name_arriving_in_two_shapes_is_not_called_readable(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """Not hypothetical: the development store has three of these right now.

    `db.client.operation.duration` arrives as a histogram *and* as a gauge —
    the poller derives one and an instrumented service sends the other under the
    same name. `system.memory.usage` and `db.client.connection.count` collide
    the same way.

    Such a name cannot be drawn as one thing, and calling it readable would send
    a reader into a screen with nothing to render. The catalogue lists it,
    names both shapes, and says it cannot draw it — which is also how somebody
    finds out the collision exists.
    """
    catalogue = await _catalogue(clickhouse, reporting)
    mixed = [metric for metric in catalogue.metrics if len(metric.shapes) > 1]

    for metric in mixed:
        assert metric.readable is False, f"{metric.metric_name} arrived as {metric.shapes}"


async def test_the_counts_survive_a_redelivered_point(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """`points` counts distinct instants per series, never rows.

    Compared against the raw row count of the same window: they differ exactly
    when a batch was redelivered, and `points` is the one that describes what
    was measured rather than what was stored.
    """
    tenant_id, project_id = reporting
    start, end = _window()
    catalogue = await _catalogue(clickhouse, reporting)

    stored = await clickhouse.query(
        "SELECT count() FROM metrics_histogram WHERE tenant_id = %(tenant)s "
        "AND project_id = %(project)s AND timestamp >= %(start)s AND timestamp < %(end)s",
        parameters={"tenant": tenant_id, "project": project_id, "start": start, "end": end},
    )
    rows = int(stored.result_rows[0][0])
    counted = sum(metric.points for metric in catalogue.metrics if metric.shapes == ["histogram"])

    # Never more than the rows: a distinct count cannot exceed what it counted.
    assert counted <= rows


async def test_another_tenant_reads_none_of_this_projects_metrics(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """Real rows, real ClickHouse, and the wrong tenant sees none of them."""
    _, project_id = reporting
    start, end = _window()

    stolen = await service.list_metrics(
        TenantScopedRepository(clickhouse, tenant_id=str(uuid.uuid4())),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )

    assert stolen.metrics == []


async def test_a_window_with_no_metrics_is_an_empty_catalogue_not_an_error(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    tenant_id, project_id = reporting
    long_ago = datetime.now(UTC) - timedelta(days=400)

    catalogue = await service.list_metrics(
        TenantScopedRepository(clickhouse, tenant_id=tenant_id),
        project_id=project_id,
        from_time=long_ago,
        to_time=long_ago + timedelta(hours=1),
    )

    assert catalogue.metrics == []


def test_the_shapes_named_here_are_the_ones_the_store_has_tables_for() -> None:
    """A shape with no table is a branch that cannot exist, and the reverse is
    a customer's metrics invisible."""
    assert {
        queries.SHAPE_GAUGE,
        queries.SHAPE_SUM,
        queries.SHAPE_HISTOGRAM,
        queries.SHAPE_EXPONENTIAL_HISTOGRAM,
        queries.SHAPE_SUMMARY,
    } >= queries.READABLE_SHAPES


# ---------------------------------------------------- the histogram itself


async def _histogram(
    clickhouse: AsyncClient,
    reporting: tuple[str, str],
    metric_name: str,
    window: tuple[datetime, datetime] | None = None,
) -> HistogramResponse:
    tenant_id, project_id = reporting
    start, end = window or _window()
    return await service.read_histogram(
        TenantScopedRepository(clickhouse, tenant_id=tenant_id),
        project_id=project_id,
        metric_name=metric_name,
        from_time=start,
        to_time=end,
    )


async def test_a_real_histogram_merges_into_one_distribution_over_its_own_bounds(
    clickhouse: AsyncClient, reporting: tuple[str, str], histogram_metric: str
) -> None:
    """A real exporter's latency, folded into one distribution. The offline suite
    cannot tell you `sumForEach` and the `argMax`/`argMin` subtraction parse, or
    that they agree about array lengths.

    **The lengths are the assertion, not the number.** This asserted fifteen
    bounds, which was true of k6's ladder and of nothing else — a fact about the
    generator that happened to be running, dressed as a fact about the read.
    """
    answer = await _histogram(clickhouse, reporting, histogram_metric)

    assert answer.merged is True, "one metric, one layout, one distribution"
    (layout,) = answer.layouts
    assert len(layout.bounds) > 0, "a histogram the read folded has bounds to fold over"
    assert len(layout.bucket_counts) == len(layout.bounds) + 1, (
        "a histogram has one more bucket than it has bounds: the last is unbounded"
    )
    assert layout.count > 0
    assert layout.series > 0
    assert layout.services


async def test_the_window_delta_is_far_below_the_number_of_points_stored(
    clickhouse: AsyncClient, reporting: tuple[str, str], histogram_metric: str
) -> None:
    """The defect this read exists to avoid, measured against the store.

    Every histogram here is cumulative, exported every ten seconds, so summing
    the points would report the same observations hundreds of times over. The
    catalogue counts points; this counts observations, and over a two-day window
    the second must be nothing like the first multiplied by the export rate.
    """
    tenant_id, project_id = reporting
    # One window, measured once and given to both sides. Calling `_window()` twice
    # put a few milliseconds of fresh points on one side of the comparison only.
    window = _window()
    start, end = window
    stored = await clickhouse.query(
        """
        SELECT sum(points_total) AS naive, max(delta) AS largest_series_delta
        FROM (
            SELECT series_hash, max(count) - min(count) AS delta, sum(count) AS points_total
            FROM metrics_histogram
            WHERE tenant_id = %(tenant)s AND project_id = %(project)s
              AND metric_name = %(metric)s
              AND timestamp >= %(since)s AND timestamp <= %(until)s
            GROUP BY series_hash
        )
        """,
        parameters={
            "tenant": tenant_id,
            "project": project_id,
            "metric": histogram_metric,
            "since": start,
            "until": end,
        },
    )
    naive, largest_series_delta = stored.result_rows[0]

    answer = await _histogram(clickhouse, reporting, histogram_metric, window)
    (layout,) = answer.layouts

    assert naive > 0, "no points to compare against"
    assert layout.count < naive, (
        f"the read returned {layout.count}, which is not below the naive sum of {naive}. "
        "Summing cumulative points counts every observation once per export."
    )
    # **A delta against a delta.** This compared the window's total against the
    # largest single *cumulative* point, which is the total since the exporter
    # started — a number the window is under whenever the exporter started before
    # it. The comparable lower bound is the biggest rise any one series made
    # inside the window: every series' rise is part of the answer, so the answer
    # cannot be smaller than the largest of them.
    assert layout.count >= largest_series_delta, (
        f"the read returned {layout.count}, below the {largest_series_delta} that one "
        "series alone rose by inside this window — so a series was dropped."
    )
    # **This is a floor, not the answer.** The read sums every series' rise, so it is
    # at least the largest of them and at most the naive total; asserting the exact sum
    # would mean recomputing the read's own grouping — `series_hash` *and* the bucket
    # bounds, skipping malformed points — here, which is a copy of the code under test
    # rather than a check on it. What this pair catches is the two ways the read can be
    # wrong in kind: counting a cumulative point as an observation, and losing a series.


async def test_percentiles_come_back_ordered_and_inside_the_bounds(
    clickhouse: AsyncClient, reporting: tuple[str, str], histogram_metric: str
) -> None:
    """Estimates, but not arbitrary ones: each must sit inside the range its own
    buckets describe, and they must not go backwards."""
    answer = await _histogram(clickhouse, reporting, histogram_metric)
    (layout,) = answer.layouts

    estimates = [layout.p50, layout.p75, layout.p90, layout.p95, layout.p99]
    assert all(estimate is not None for estimate in estimates)
    assert estimates == sorted(estimate for estimate in estimates if estimate is not None)
    assert all(0.0 <= estimate <= layout.bounds[-1] for estimate in estimates if estimate)


async def test_a_metric_reported_with_two_bucket_layouts_is_never_merged(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """This is real, not a constructed case.

    `db.client.operation.duration` arrives from `product-catalog` with bounds
    `[0, 5, 10, ...]` and from `accounting` with `[0.001, 0.005, 0.01, ...]` —
    one in milliseconds and one in seconds. A reader that added their buckets
    would publish a latency distribution that is the sum of two different units.
    """
    answer = await _histogram(clickhouse, reporting, "db.client.operation.duration")

    if len(answer.layouts) < 2:
        pytest.skip(
            "the store currently holds one bucket layout for this metric; the "
            "conflict this asserts on is a property of the demo's instrumentation"
        )

    assert answer.merged is False
    layouts = {len(layout.bounds) for layout in answer.layouts}
    assert len(layouts) > 1, "two layouts must differ in the bounds they carry"
    for layout in answer.layouts:
        assert layout.services, "a layout must name who reported it, so it can be fixed"
        assert len(layout.bucket_counts) == len(layout.bounds) + 1


async def test_a_metric_nobody_sent_is_an_empty_distribution_not_a_failure(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    answer = await _histogram(clickhouse, reporting, f"absent.metric.{uuid.uuid4().hex}")

    assert answer.layouts == []
    assert answer.merged is True


# ------------------------------------------------ the conditional heatmap


async def _heatmap(
    clickhouse: AsyncClient, reporting: tuple[str, str], service_name: str
) -> DurationHeatmapResponse:
    tenant_id, project_id = reporting
    start, end = _window()
    return await service.read_duration_heatmap(
        TenantScopedRepository(clickhouse, tenant_id=tenant_id),
        project_id=project_id,
        service_name=service_name,
        from_time=start,
        to_time=end,
    )


async def _a_service_sending_histograms(clickhouse: AsyncClient, reporting: tuple[str, str]) -> str:
    tenant_id, project_id = reporting
    result = await clickhouse.query(
        "SELECT service_name, count() AS rows FROM metrics_histogram "
        "WHERE tenant_id = %(tenant)s AND project_id = %(project)s "
        "AND metric_name IN %(names)s AND timestamp >= %(since)s "
        "GROUP BY service_name ORDER BY rows DESC LIMIT 1",
        parameters={
            "tenant": tenant_id,
            "project": project_id,
            "names": list(queries.DURATION_METRIC_NAMES),
            "since": _window()[0],
        },
    )
    assert result.result_rows, (
        "no service in the busiest project sends an OTLP server-duration histogram, so the "
        "conditional panel has nothing to be conditional on. Run `just otel-demo up`."
    )
    return str(result.result_rows[0][0])


async def test_a_service_sending_histograms_gets_a_heatmap_over_its_own_bounds(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """OD7's panel, against the real thing.

    The offline suite cannot tell you `lagInFrame` parses, that the window
    partition matches the grouping, or that the arrays it subtracts line up.
    """
    service_name = await _a_service_sending_histograms(clickhouse, reporting)

    answer = await _heatmap(clickhouse, reporting, service_name)

    assert answer.available is True
    assert answer.metric_name in queries.DURATION_METRIC_NAMES
    assert answer.bounds, "a heatmap needs the bounds its rows are drawn against"
    assert answer.columns, "the service reported inside this window"
    for column in answer.columns:
        assert len(column.counts) == len(answer.bounds) + 1, (
            "a histogram has one more bucket than it has bounds: the last is unbounded"
        )
        assert all(count >= 0 for count in column.counts)


async def test_the_first_column_is_a_delta_and_not_the_whole_history(
    clickhouse: AsyncClient, reporting: tuple[str, str], histogram_metric: str
) -> None:
    """The defect the extra read exists to prevent.

    These points are cumulative, so a first column with no predecessor carries
    everything since the exporter started. Measured on the development store it
    read 24,845 against 500 to 1,300 for every column after it — on a heatmap,
    one black stripe and the rest empty.

    **The property only holds for a series that predates the window**, and this
    asserted it unconditionally until 2026-09-14, when the demo's exporter had
    restarted four times inside a two-day window. Every series then began
    *inside* it, so the first column each appeared in legitimately carried its
    whole accumulation — and the test failed on correct behaviour. It now checks
    the premise and says so rather than failing on data.
    """
    tenant_id, project_id = reporting
    start, _ = _window()

    predating = await clickhouse.query(
        "SELECT count() FROM metrics_histogram WHERE tenant_id = %(tenant)s "
        "AND project_id = %(project)s AND metric_name = %(metric)s "
        "AND timestamp >= %(since)s AND start_timestamp < %(since)s",
        parameters={
            "tenant": tenant_id,
            "project": project_id,
            "metric": histogram_metric,
            "since": start,
        },
    )
    if not int(predating.result_rows[0][0]):
        pytest.skip(
            "every series in this window began inside it, so no column has a "
            "predecessor to subtract and there is no pre-window history to leak"
        )

    answer = await _histogram(clickhouse, reporting, histogram_metric)
    heatmap = await _heatmap(clickhouse, reporting, "frontend")
    totals = [column.total for column in heatmap.columns if column.total > 0]

    assert len(totals) > 2, "not enough columns to compare the first against the rest"
    rest = sorted(totals[1:])
    assert totals[0] <= rest[len(rest) // 2] * 10, (
        f"the first column holds {totals[0]} against a median of {rest[len(rest) // 2]}. "
        "That is the whole history since start-up, not this bucket's own traffic."
    )
    assert answer.layouts, "the metric this compares against reported nothing"


async def test_a_service_sending_no_histogram_has_no_panel_rather_than_an_empty_one(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """OD7 says absent, not empty.

    An empty heatmap and a service with no traffic look identical once drawn, so
    the screen has to be told which it is holding.
    """
    answer = await _heatmap(clickhouse, reporting, f"absent-service-{uuid.uuid4().hex}")

    assert answer.available is False
    assert answer.columns == []
    assert answer.bounds == []


# ------------------------------------------------ the narrowed catalogue


async def _narrowed(
    clickhouse: AsyncClient,
    reporting: tuple[str, str],
    **narrowing: object,
) -> MetricCatalogueResponse:
    tenant_id, project_id = reporting
    start, end = _window()
    return await service.list_metrics(
        TenantScopedRepository(clickhouse, tenant_id=tenant_id),
        project_id=project_id,
        from_time=start,
        to_time=end,
        **narrowing,  # type: ignore[arg-type]
    )


async def test_every_narrowing_of_the_catalogue_parses(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """The offline suite drives a fake store and cannot parse SQL.

    `catalogue_sql` splices clauses into a five-table union by string
    replacement, so a misplaced `HAVING` or a stray `AND` is a runtime error
    that every offline test would pass straight over. This asks ClickHouse.
    """
    for narrowing in (
        {"name_match": "http"},
        {"shape": "histogram"},
        {"readable_only": True},
        {"name_match": "http", "shape": "histogram", "readable_only": True},
    ):
        answer = await _narrowed(clickhouse, reporting, **narrowing)

        assert isinstance(answer.metrics, list), f"{narrowing} did not return a catalogue"


async def test_narrowing_by_name_returns_a_subset_and_only_matches(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    whole = await _narrowed(clickhouse, reporting)
    matching = await _narrowed(clickhouse, reporting, name_match="http")

    assert whole.metrics, "the busiest project reported no metrics at all"
    assert len(matching.metrics) <= len(whole.metrics)
    assert all("http" in metric.metric_name for metric in matching.metrics)


async def test_narrowing_by_shape_keeps_a_name_that_arrived_in_two(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """Shape is a `HAVING` over the grouped shapes, never a `WHERE` over rows.

    Dropping rows would drop the other shapes a name arrived in, and `readable`
    — computed from whatever survived — would then offer a chart that cannot be
    drawn. `db.client.operation.duration` really does arrive as both a gauge and
    a histogram in the development store, which is what makes this checkable.
    """
    histograms = await _narrowed(clickhouse, reporting, shape="histogram")

    mixed = [metric for metric in histograms.metrics if len(metric.shapes) > 1]
    if not mixed:
        pytest.skip("no name in this store arrived in more than one shape")

    for metric in mixed:
        assert "histogram" in metric.shapes
        # The other shape survived the filter, so `readable` still sees it.
        assert metric.readable is all(shape in queries.READABLE_SHAPES for shape in metric.shapes)


async def test_readable_only_returns_nothing_this_console_cannot_draw(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    readable = await _narrowed(clickhouse, reporting, readable_only=True)

    assert readable.metrics, "the store holds histograms, so something must be drawable"
    assert all(metric.readable for metric in readable.metrics)

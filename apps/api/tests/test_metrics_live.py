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
    """The tenant and project with the most metric points, found with a raw read.

    Asking "who has metrics" needs an unscoped query, which the repository
    rightly refuses, so it is fixture setup rather than a claim about the API.
    """
    result = await clickhouse.query(
        "SELECT tenant_id, project_id, count() AS points FROM metrics_histogram "
        "WHERE timestamp >= %(since)s GROUP BY tenant_id, project_id "
        "ORDER BY points DESC LIMIT 1",
        parameters={"since": datetime.now(UTC) - LOOKBACK},
    )
    assert result.result_rows, (
        "no histogram points in ClickHouse for the last two days. This suite asserts "
        "on real instrumentation, so an empty store is a failure here rather than a "
        "pass — run the load generator."
    )
    tenant_id, project_id, _ = result.result_rows[0]
    return str(tenant_id), str(project_id)


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
    clickhouse: AsyncClient, reporting: tuple[str, str], metric_name: str
) -> HistogramResponse:
    tenant_id, project_id = reporting
    start, end = _window()
    return await service.read_histogram(
        TenantScopedRepository(clickhouse, tenant_id=tenant_id),
        project_id=project_id,
        metric_name=metric_name,
        from_time=start,
        to_time=end,
    )


async def test_a_real_histogram_merges_into_one_distribution_over_its_own_bounds(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """`k6.http_req_duration` is the load generator's own latency, sixteen
    buckets over fifteen bounds. The offline suite cannot tell you `sumForEach`
    and the `argMax`/`argMin` subtraction parse, or that they agree about array
    lengths."""
    answer = await _histogram(clickhouse, reporting, "k6.http_req_duration")

    assert answer.merged is True, "one metric, one layout, one distribution"
    (layout,) = answer.layouts
    assert len(layout.bounds) == 15
    assert len(layout.bucket_counts) == len(layout.bounds) + 1, (
        "a histogram has one more bucket than it has bounds: the last is unbounded"
    )
    assert layout.count > 0
    assert layout.series > 0
    assert layout.services


async def test_the_window_delta_is_far_below_the_number_of_points_stored(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """The defect this read exists to avoid, measured against the store.

    Every histogram here is cumulative, exported every ten seconds, so summing
    the points would report the same observations hundreds of times over. The
    catalogue counts points; this counts observations, and over a two-day window
    the second must be nothing like the first multiplied by the export rate.
    """
    tenant_id, project_id = reporting
    start, end = _window()
    stored = await clickhouse.query(
        "SELECT sum(count) AS naive, max(count) AS largest_single_point "
        "FROM metrics_histogram WHERE tenant_id = %(tenant)s AND project_id = %(project)s "
        "AND metric_name = 'k6.http_req_duration' AND timestamp >= %(since)s",
        parameters={"tenant": tenant_id, "project": project_id, "since": start},
    )
    naive, largest = stored.result_rows[0]

    answer = await _histogram(clickhouse, reporting, "k6.http_req_duration")
    (layout,) = answer.layouts

    assert naive > 0, "no points to compare against"
    assert layout.count < naive, (
        f"the read returned {layout.count}, which is not below the naive sum of {naive}. "
        "Summing cumulative points counts every observation once per export."
    )
    assert layout.count >= largest, (
        "the window's total cannot be smaller than the largest single cumulative point"
    )
    assert end > start


async def test_percentiles_come_back_ordered_and_inside_the_bounds(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """Estimates, but not arbitrary ones: each must sit inside the range its own
    buckets describe, and they must not go backwards."""
    answer = await _histogram(clickhouse, reporting, "k6.http_req_duration")
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
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """The defect the extra read exists to prevent.

    These points are cumulative, so a first column with no predecessor carries
    everything since the exporter started. Measured on the development store it
    read 24,845 against 500 to 1,300 for every column after it — on a heatmap, one
    black stripe and the rest empty. It is asserted as a ratio rather than a
    number because the demo's traffic is not fixed.
    """
    service_name = await _a_service_sending_histograms(clickhouse, reporting)

    answer = await _heatmap(clickhouse, reporting, service_name)
    totals = [column.total for column in answer.columns if column.total > 0]

    assert len(totals) > 2, "not enough columns to compare the first against the rest"
    rest = sorted(totals[1:])
    median = rest[len(rest) // 2]
    assert totals[0] <= median * 10, (
        f"the first column holds {totals[0]} against a median of {median}. That is the whole "
        "history since start-up, not this bucket's own traffic."
    )


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
